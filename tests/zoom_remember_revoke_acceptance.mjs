import { mkdir } from "node:fs/promises";
import { chromium } from "../web/node_modules/playwright-core/index.mjs";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const baseURL = requireAcceptanceBaseURL();
const browserPath = process.env.CAELIAE_READ_BROWSER_PATH ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const annotationId = process.env.CAELIAE_READ_REMEMBER_ANNOTATION_ID ?? "9ecd0074-6f80-45cb-8649-eb77eefaff22";
const outputDir = process.env.CAELIAE_READ_REVOKE_OUTPUT ?? ".artifacts/zoom-remember-revoke";
await mkdir(outputDir, { recursive: true });

const browser = await chromium.launch({ headless: true, executablePath: browserPath });
try {
  const page = await browser.newPage({ viewport: { width: 800, height: 1024 } });
  const { papers } = await (await page.request.get(`${baseURL}/api/papers`)).json();
  let target;
  for (const paper of papers) {
    const { annotations } = await (await page.request.get(`${baseURL}/api/annotations?paper_id=${paper.paper_id}`)).json();
    const annotation = annotations.find((item) => item.annotation_id === annotationId);
    if (annotation) { target = { paper, annotation }; break; }
  }
  if (!target?.annotation.remember || target.annotation.author !== "assistant") throw new Error("target is not an existing assistant remember");

  await page.goto(`${baseURL}/reader/`);
  await page.getByRole("button", { name: /^论文/ }).click();
  await page.locator(".paper-drawer .paper-card", { hasText: target.paper.original_filename }).click();
  const mark = page.locator(`[data-annotation-id="${annotationId}"]`).first();
  await mark.waitFor();

  const inspectAlignment = async (zoom) => {
    const expectedLabel = `${zoom}%`;
    const handle = page.locator(".toolbar-reveal-handle:visible");
    if (await handle.count()) await handle.first().click();
    await page.locator(".pdf-zoom-controls").waitFor();
    while (await page.locator(".pdf-zoom-controls > span").textContent() !== expectedLabel) await page.getByRole("button", { name: "＋" }).click();
    await page.waitForTimeout(350);
    await mark.scrollIntoViewIfNeeded();
    const measured = await mark.evaluate((node, quad) => {
      const canvas = document.querySelector(".pdf-page canvas");
      const layer = document.querySelector(".text-layer");
      const pageNode = document.querySelector(".pdf-page");
      const canvasRect = canvas.getBoundingClientRect();
      const layerRect = layer.getBoundingClientRect();
      const pageRect = pageNode.getBoundingClientRect();
      const markRect = node.getBoundingClientRect();
      const underline = getComputedStyle(node, "::after");
      const highlight = getComputedStyle(node, "::before");
      return {
        canvas: { width: canvasRect.width, height: canvasRect.height },
        textLayer: { width: layerRect.width, height: layerRect.height, dx: layerRect.left - canvasRect.left, dy: layerRect.top - canvasRect.top },
        mark: { x: (markRect.left - pageRect.left) / pageRect.width, y: (markRect.top - pageRect.top) / pageRect.height, width: markRect.width / pageRect.width },
        expected: { x: quad.x, y: quad.y, width: quad.width },
        underline: { color: underline.backgroundColor, left: underline.left, right: underline.right, bottom: underline.bottom, width: underline.width, height: underline.height },
        highlight: { color: highlight.backgroundColor, opacity: highlight.opacity, left: highlight.left, right: highlight.right, top: highlight.top, bottom: highlight.bottom, width: highlight.width, height: highlight.height },
      };
    }, target.annotation.normalized_quads[0]);
    if (Math.abs(measured.canvas.width - measured.textLayer.width) > 0.5 || Math.abs(measured.canvas.height - measured.textLayer.height) > 0.5 || Math.abs(measured.textLayer.dx) > 0.5 || Math.abs(measured.textLayer.dy) > 0.5) throw new Error(`${zoom}% canvas/text layer drift`);
    if (Math.abs(measured.mark.x - measured.expected.x) > 0.01 || Math.abs(measured.mark.y - measured.expected.y) > 0.012 || Math.abs(measured.mark.width - measured.expected.width) > 0.01) throw new Error(`${zoom}% annotation anchor drift`);
    if (measured.underline.color !== "rgb(128, 147, 164)" || measured.highlight.color !== "rgb(216, 201, 120)" || Number(measured.highlight.opacity) > .05) throw new Error(`${zoom}% default annotation styling failed`);
    return measured;
  };

  const at150 = await inspectAlignment(150);
  await page.screenshot({ path: `${outputDir}/01-remember-150.png`, fullPage: true });
  const at200 = await inspectAlignment(200);
  await page.screenshot({ path: `${outputDir}/02-remember-200.png`, fullPage: true });

  await mark.click();
  const activeHighlightOpacity = await mark.evaluate((node) => Number(getComputedStyle(node, "::before").opacity));
  if (activeHighlightOpacity < .2) throw new Error("active annotation highlight did not appear");
  const card = page.locator(".annotation-card.card-assistant");
  const noteBefore = await card.locator("p").textContent();
  const responsePromise = page.waitForResponse((response) => response.url().includes(`/${annotationId}/remember/revoke`) && response.request().method() === "POST");
  await card.getByRole("button", { name: "取消记住" }).click();
  const revokeResponse = await responsePromise;
  if (!revokeResponse.ok()) throw new Error(`remember revocation returned ${revokeResponse.status()}`);
  await page.locator(`[data-annotation-id="${annotationId}"]:not(.annotation-remember)`).first().waitFor();

  const after = await page.locator(`[data-annotation-id="${annotationId}"]`).first().evaluate((node) => ({
    className: node.className,
    underlineColor: getComputedStyle(node, "::after").backgroundColor,
    highlightColor: getComputedStyle(node, "::before").backgroundColor,
    highlightOpacity: getComputedStyle(node, "::before").opacity,
  }));
  const cardAfter = page.locator(".annotation-card.card-assistant");
  if (after.className.includes("annotation-remember") || after.underlineColor !== "rgb(128, 147, 164)") throw new Error("yellow highlight removal changed the assistant underline");
  if (await cardAfter.getByText("AI 批注").count() !== 1 || await cardAfter.getByText(noteBefore).count() !== 1 || await cardAfter.getByRole("button", { name: /删除/ }).count() !== 0) throw new Error("AI card changed or exposed deletion after remember revocation");
  if (await cardAfter.getByRole("button", { name: "取消记住" }).count() !== 0) throw new Error("revoke action remained available after success");

  const listed = await (await page.request.get(`${baseURL}/api/annotations?paper_id=${target.paper.paper_id}`)).json();
  const persisted = listed.annotations.find((item) => item.annotation_id === annotationId);
  if (!persisted || persisted.remember || persisted.author !== "assistant" || persisted.note !== noteBefore) throw new Error("persisted annotation semantics changed beyond remember=false");
  const deleteAttempt = await page.request.delete(`${baseURL}/api/annotations/${annotationId}?session_id=${persisted.session_id}`);
  if ((await deleteAttempt.json()).deleted !== false) throw new Error("assistant annotation became deletable");
  await page.screenshot({ path: `${outputDir}/03-remember-revoked.png`, fullPage: true });

  console.log(JSON.stringify({ annotation_id: annotationId, paper: target.paper.original_filename, at150, at200, revoke_status: revokeResponse.status(), after, persisted: { author: persisted.author, remember: persisted.remember, note: persisted.note, normalized_quads: persisted.normalized_quads }, assistant_delete_attempt: false }, null, 2));
} finally {
  await browser.close();
}
