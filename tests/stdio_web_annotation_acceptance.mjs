import { spawnSync } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { chromium } from "../web/node_modules/playwright-core/index.mjs";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const baseURL = requireAcceptanceBaseURL();
const browserPath = process.env.CAELIAE_READ_BROWSER_PATH ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const outputDir = process.env.CAELIAE_READ_STDIO_WEB_OUTPUT ?? ".artifacts/stdio-web-annotation";
await mkdir(outputDir, { recursive: true });

const browser = await chromium.launch({ headless: true, executablePath: browserPath });
try {
  const page = await browser.newPage({ viewport: { width: 800, height: 1024 } });
  const { papers } = await (await page.request.get(`${baseURL}/api/papers`)).json();
  let target;
  for (const paper of papers) {
    const { annotations } = await (await page.request.get(`${baseURL}/api/annotations?paper_id=${paper.paper_id}`)).json();
    if (annotations?.length) { target = { paper, anchor: annotations[0] }; break; }
  }
  if (!target) throw new Error("no real paper with an existing anchor was found");

  await page.goto(`${baseURL}/reader/`);
  await page.getByRole("button", { name: /^论文/ }).click();
  await page.locator(".paper-drawer .paper-card", { hasText: target.paper.original_filename }).click();
  await page.locator(".annotation-mark").first().waitFor();
  await page.locator(".sync-saved").waitFor();

  const beforeCount = await page.locator(".annotation-mark.annotation-assistant.annotation-remember").count();
  const note = `stdio assistant remember ${new Date().toISOString()}`;
  const idempotencyKey = `stdio-web-${crypto.randomUUID()}`;
  const request = { anchor: target.anchor, note, idempotency_key: idempotencyKey };
  const startedAt = Date.now();
  const invoked = spawnSync(
    ".venv/Scripts/python.exe",
    ["tests/stdio_create_assistant_annotation.py"],
    { cwd: process.cwd(), input: Buffer.from(JSON.stringify(request), "utf8").toString("base64"), encoding: "utf8", env: process.env },
  );
  if (invoked.status !== 0) throw new Error(`stdio helper failed: ${invoked.stderr}`);
  const encodedResult = invoked.stdout.trim().split(/\r?\n/).at(-1);
  const stdio = JSON.parse(Buffer.from(encodedResult, "base64").toString("utf8"));
  const created = stdio.first.annotation;
  if (!stdio.first.created || stdio.first.replayed || !stdio.replay.replayed) throw new Error("stdio idempotency flags are incorrect");
  if (created.annotation_id !== stdio.replay.annotation.annotation_id || stdio.persisted_matches.length !== 1) throw new Error("stdio replay persisted more than one annotation");

  const mark = page.locator(`.annotation-mark.annotation-assistant.annotation-remember[data-annotation-id="${created.annotation_id}"]`).first();
  await mark.waitFor({ timeout: 7000 });
  const appearedAfterMs = Date.now() - startedAt;
  const rendered = await mark.evaluate((node, expected) => {
    const pageRect = document.querySelector(".pdf-page").getBoundingClientRect();
    const rect = node.getBoundingClientRect();
    return {
      className: node.className,
      authorLabel: node.getAttribute("aria-label"),
      underlineColor: getComputedStyle(node, "::after").backgroundColor,
      rememberColor: getComputedStyle(node, "::before").backgroundColor,
      rememberOpacity: getComputedStyle(node, "::before").opacity,
      normalized: {
        x: (rect.left - pageRect.left) / pageRect.width,
        width: rect.width / pageRect.width,
      },
      expected: { x: expected.x, width: expected.width },
    };
  }, created.normalized_quads[0]);
  if (rendered.underlineColor !== "rgb(128, 147, 164)" || rendered.rememberColor !== "rgb(216, 201, 120)" || Number(rendered.rememberOpacity) > 0.05) throw new Error("assistant remember default styling is too strong");
  if (Math.abs(rendered.normalized.x - rendered.expected.x) > 0.01 || Math.abs(rendered.normalized.width - rendered.expected.width) > 0.01) throw new Error("rendered annotation anchor drifted");

  await mark.click();
  const activeRememberOpacity = await mark.evaluate((node) => Number(getComputedStyle(node, "::before").opacity));
  if (activeRememberOpacity < 0.2) throw new Error("assistant remember active highlight did not appear");
  const card = page.locator(".annotation-card.card-assistant");
  await card.getByText("AI 批注").waitFor();
  await card.getByText(note).waitFor();
  if (await card.getByRole("button", { name: /删除/ }).count()) throw new Error("assistant annotation incorrectly exposed user deletion");
  await page.screenshot({ path: `${outputDir}/assistant-remember-visible.png`, fullPage: true });

  console.log(JSON.stringify({
    transport: "stdio",
    tool: "create_assistant_annotation",
    paper: target.paper.original_filename,
    annotation_id: created.annotation_id,
    thread_id: created.thread_id,
    author: created.author,
    remember: created.remember,
    note: created.note,
    first_replayed: stdio.first.replayed,
    replay_replayed: stdio.replay.replayed,
    persisted_match_count: stdio.persisted_matches.length,
    browser_marks_before: beforeCount,
    browser_marks_after: await page.locator(".annotation-mark.annotation-assistant.annotation-remember").count(),
    appeared_after_ms: appearedAfterMs,
    rendered,
  }, null, 2));
} finally {
  await browser.close();
}
