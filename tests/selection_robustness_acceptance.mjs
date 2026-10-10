import assert from "node:assert/strict";
import { mkdir, stat } from "node:fs/promises";
import { chromium } from "../web/node_modules/playwright-core/index.mjs";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const baseURL = requireAcceptanceBaseURL();
const fixturePath = process.env.CAELIAE_READ_ACCEPTANCE_PDF;
if (!fixturePath) throw new Error("CAELIAE_READ_ACCEPTANCE_PDF is required");
const browserPath = process.env.CAELIAE_READ_BROWSER_PATH
  ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const outputDir = process.env.CAELIAE_READ_SELECTION_OUTPUT ?? ".artifacts/selection-robustness";
await mkdir(outputDir, { recursive: true });

const browser = await chromium.launch({ headless: true, executablePath: browserPath });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(`${baseURL}/reader/`);
  await page.locator('input[type="file"]').setInputFiles(fixturePath);
  await page.getByText("1 / 2").waitFor();
  await page.getByRole("button", { name: "下一页" }).click();
  await page.getByText("2 / 2").waitFor();
  const target = page.locator(".text-layer span").filter({ hasText: "Selected passage" }).first();
  await target.waitFor();

  const identity = await page.evaluate(() => ({
    paperId: localStorage.getItem("caeliae.read.activePaperId"),
    sessionId: localStorage.getItem("caeliae.read.activeSessionId"),
  }));
  assert(identity.paperId && identity.sessionId, "Reader identity was not persisted");
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const response = await page.request.get(`${baseURL}/api/sessions/${identity.sessionId}/context`);
    if (response.ok() && (await response.json()).page_number === 2) break;
    if (attempt === 39) throw new Error("Reader context did not reach page 2");
    await page.waitForTimeout(100);
  }

  const geometry = await target.evaluate((span) => {
    const rect = span.getBoundingClientRect();
    const pageRect = span.closest(".pdf-page").getBoundingClientRect();
    return {
      text: span.textContent,
      quad: {
        x: (rect.left - pageRect.left) / pageRect.width,
        y: (rect.top - pageRect.top) / pageRect.height,
        width: rect.width / pageRect.width,
        height: rect.height / pageRect.height,
      },
      pageWidth: pageRect.width,
      pageHeight: pageRect.height,
    };
  });
  const created = await page.request.post(`${baseURL}/api/annotations/user`, {
    data: {
      paper_id: identity.paperId,
      session_id: identity.sessionId,
      page_number: 2,
      exact_text: geometry.text,
      prefix: "",
      suffix: "",
      normalized_quads: [geometry.quad],
      page_width: geometry.pageWidth,
      page_height: geometry.pageHeight,
      rotation: 0,
      note: "selection robustness acceptance",
      style_key: "primary",
      mark_type: "highlight",
      idempotency_key: crypto.randomUUID(),
    },
  });
  assert.equal(created.status(), 201, await created.text());
  const annotationId = (await created.json()).annotation.annotation_id;

  await page.reload();
  await page.getByText("2 / 2").waitFor();
  const mark = page.locator(`.annotation-mark[data-annotation-id="${annotationId}"]`);
  await mark.waitFor();
  const reselectTarget = page.locator(".text-layer span").filter({ hasText: "Selected passage" }).first();
  const targetBox = await reselectTarget.boundingBox();
  assert(targetBox, "annotated text target was not measurable");
  const centerHit = await reselectTarget.evaluate((span) => {
    const rect = span.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return { tag: hit?.tagName, inTextLayer: Boolean(hit?.closest(".text-layer")) };
  });
  assert(centerHit.inTextLayer, `annotation quad intercepted text hit-testing (${centerHit.tag})`);

  await page.mouse.move(targetBox.x + 2, targetBox.y + targetBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(targetBox.x + targetBox.width - 2, targetBox.y + targetBox.height / 2, { steps: 12 });
  await page.mouse.up();
  await page.locator(".selection-composer").waitFor();
  const reselectedText = await page.evaluate(() => document.getSelection()?.toString() ?? "");
  assert(reselectedText.includes("Selected passage"), "annotated text was not reselectable");
  await page.keyboard.press("Escape");

  const entryDot = mark.locator(".annotation-entry-dot");
  await entryDot.click();
  await page.locator(".annotation-card").waitFor();
  assert.equal(await page.locator(".annotation-card").count(), 1, "annotation entry point no longer opens its card");
  await page.close();

  const longPdfPath = `${outputDir}/long-selection.pdf`;
  const maker = await browser.newPage();
  const rows = Array.from(
    { length: 72 },
    (_, index) => `<p>Selection diagnostic line ${String(index + 1).padStart(3, "0")} carries stable selectable text across the synthetic page.</p>`,
  ).join("");
  await maker.setContent(`<style>@page{size:8.5in 20in;margin:.55in}body{font:14px/1.45 Arial;margin:0}p{margin:0 0 5px}</style>${rows}`);
  await maker.pdf({ path: longPdfPath, width: "8.5in", height: "20in", printBackground: true });
  await maker.close();
  assert((await stat(longPdfPath)).size > 1_000, "synthetic long PDF was not generated");

  const longPage = await browser.newPage({ viewport: { width: 900, height: 650 } });
  await longPage.goto(`${baseURL}/reader/`);
  await longPage.locator('input[type="file"]').setInputFiles(longPdfPath);
  await longPage.getByText("1 / 2").waitFor();
  const firstLine = longPage.locator(".text-layer span").filter({ hasText: "line 001" }).first();
  await firstLine.waitFor();
  const stageBox = await longPage.locator(".page-stage").boundingBox();
  const firstBox = await firstLine.boundingBox();
  assert(stageBox && firstBox, "long-selection geometry was not measurable");

  await longPage.mouse.move(firstBox.x + 5, firstBox.y + firstBox.height / 2);
  await longPage.mouse.down();
  const edgeX = stageBox.x + stageBox.width * 0.62;
  const edgeY = stageBox.y + stageBox.height - 4;
  for (let step = 0; step < 28; step += 1) {
    await longPage.mouse.move(edgeX, edgeY + (step % 2 ? 1 : -1));
    await longPage.waitForTimeout(70);
  }
  const edgeState = await longPage.evaluate(({ x, y }) => {
    const stage = document.querySelector(".page-stage");
    const layer = document.querySelector(".text-layer");
    return {
      scrollTop: stage.scrollTop,
      scrollMax: stage.scrollHeight - stage.clientHeight,
      pointerBelowLayer: y > layer.getBoundingClientRect().bottom,
      hitClass: document.elementFromPoint(x, y)?.className ?? "",
    };
  }, { x: edgeX, y: edgeY });
  await longPage.mouse.up();
  await longPage.locator(".selection-composer").waitFor();
  const longSelection = await longPage.evaluate(() => document.getSelection()?.toString() ?? "");
  assert(edgeState.scrollTop >= edgeState.scrollMax - 8, "downward selection did not autoscroll to the page bottom");
  assert(edgeState.pointerBelowLayer, "regression did not reach the page-stage bottom padding");
  assert(longSelection.includes("line 001") && longSelection.includes("line 071"), "downward selection stopped before the final rendered line");

  console.log(JSON.stringify({
    annotated_text_reselected: reselectedText,
    annotation_entry_click_preserved: true,
    long_selection_chars: longSelection.length,
    long_selection_reached: "line 071",
    edge_state: edgeState,
  }, null, 2));
  await longPage.close();
} finally {
  await browser.close();
}
