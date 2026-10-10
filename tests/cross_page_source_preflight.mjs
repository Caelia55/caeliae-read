// Real isolated automatic cross-page focus acceptance.
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { chromium } from "../web/node_modules/playwright-core/index.mjs";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const baseURL = requireAcceptanceBaseURL();
if (!process.env.CAELIAE_READ_DATA_ROOT) throw new Error("explicit isolated test data root required");
const browser = await chromium.launch({ headless: true, executablePath: process.env.CAELIAE_READ_BROWSER_PATH ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" });
await mkdir(process.env.CAELIAE_READ_LAYOUT_OUTPUT, { recursive: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const { papers } = await (await page.request.get(`${baseURL}/api/papers`)).json();
  const paper = papers.find(p => p.original_filename === "migration-real-paper.pdf");
  assert(paper?.page_count > 1);
  const { annotations } = await (await page.request.get(`${baseURL}/api/annotations?paper_id=${paper.paper_id}`)).json();
  const assistant = annotations.find(a => a.author === "assistant" && a.page_number === 1);
  assert(assistant);
  await page.goto(`${baseURL}/reader/`);
  await page.locator(".paper-card", { hasText: paper.original_filename }).click();
  await page.locator(".text-layer span").first().waitFor();
  const session = await page.evaluate(() => localStorage.getItem("caeliae.read.activeSessionId"));
  let saved = false;
  for (let attempt = 0; attempt < 40; attempt++) {
    const context = await page.request.get(`${baseURL}/api/sessions/${session}/context`);
    if (context.ok() && (await context.json()).checkpoint.page_number === 1) { saved = true; break; }
    await page.waitForTimeout(250);
  }
  assert(saved, "isolated page-1 checkpoint not saved");
  const response = await page.request.post(`${baseURL}/api/annotations/user`, { data: {
    ...assistant, session_id: session, note: "isolated cross-page same-anchor probe",
    remember: false, idempotency_key: crypto.randomUUID(),
  } });
  assert(response.ok(), `isolated user annotation: ${response.status()} ${await response.text()}`);
  const userId = (await response.json()).annotation.annotation_id;
  const records = [];
  const ensureToolbarExpanded = async () => {
    const handle = page.locator(".toolbar-reveal-handle:visible");
    if (await handle.count()) await handle.first().click();
    await page.locator(".pdf-zoom-controls").waitFor();
  };
  const snapshot = () => page.evaluate(() => {
    const workspace = document.querySelector(".workspace"), stage = document.querySelector(".page-stage");
    const mark = document.querySelector(".annotation-mark.is-focused");
    const rect = mark?.getBoundingClientRect(), viewport = stage.getBoundingClientRect();
    const pdf = document.querySelector(".pdf-page").getBoundingClientRect();
    return { activeAnnotationId: workspace.dataset.activeAnnotationId, cardOpen: workspace.dataset.annotationCardOpen,
      paperId: localStorage.getItem("caeliae.read.activePaperId"), sessionId: localStorage.getItem("caeliae.read.activeSessionId"),
      visibleCardCount: document.querySelectorAll(".annotation-card").length,
      sourceButtonCount: document.querySelectorAll(".annotation-card-source").length,
      pageLabel: document.querySelector(".pdf-page canvas")?.getAttribute("aria-label"), scrollTop: stage.scrollTop,
      focusedMarkId: mark?.dataset.annotationId ?? null,
      focusedQuadVisible: Boolean(rect && rect.bottom > viewport.top && rect.top < viewport.bottom),
      focusedNormalizedQuad: rect ? { x: (rect.left-pdf.left)/pdf.width, y: (rect.top-pdf.top)/pdf.height, width: rect.width/pdf.width } : null,
      scrollHeight: stage.scrollHeight, clientHeight: stage.clientHeight,
      zoom: document.querySelector(".pdf-zoom-controls > span")?.textContent };
  });
  for (const zoom of [125, 200]) {
    if (await page.locator('.pdf-page canvas[aria-label="论文第 1 页"]').count() === 0) {
      await page.getByRole("button", { name: "上一页" }).click();
      await page.locator('.pdf-page canvas[aria-label="论文第 1 页"]').waitFor();
    }
    await ensureToolbarExpanded();
    while (await page.locator(".pdf-zoom-controls > span").textContent() !== `${zoom}%`) await page.getByRole("button", { name: "＋" }).click();
    await page.locator(`[data-annotation-id="${userId}"]`).first().waitFor({ timeout: 7000 });
    await page.locator(`[data-annotation-id="${userId}"]`).first().locator(".annotation-entry-dot").click();
    assert.equal(await page.locator(".annotation-switcher button").count(), 2);
    await page.locator(".annotation-switcher button").filter({ hasText: "AI" }).click();
    await page.getByRole("button", { name: "回到原文" }).click();
    await page.waitForTimeout(500);
    const atSource = await snapshot();
    assert.equal(atSource.activeAnnotationId, assistant.annotation_id);
    assert.equal(atSource.visibleCardCount, 1);
    assert(atSource.focusedQuadVisible);
    const quad = assistant.normalized_quads[0];
    assert(Math.abs(atSource.focusedNormalizedQuad.x - quad.x) < .01);
    assert(Math.abs(atSource.focusedNormalizedQuad.width - quad.width) < .01);
    assert(Math.abs(atSource.focusedNormalizedQuad.y - quad.y) < .015);
    await page.getByRole("button", { name: "下一页" }).click();
    await page.locator('.pdf-page canvas[aria-label="论文第 2 页"]').waitFor();
    await page.waitForTimeout(300);
    const differentPage = await snapshot();
    assert.equal(differentPage.activeAnnotationId, assistant.annotation_id);
    assert.equal(differentPage.sourceButtonCount, 1);
    assert.equal(differentPage.visibleCardCount, 1);
    assert.equal(differentPage.cardOpen, "true");
    await page.getByRole("button", { name: "回到原文" }).click();
    await page.locator('.pdf-page canvas[aria-label="论文第 1 页"]').waitFor();
    await page.locator(`[data-annotation-id="${assistant.annotation_id}"].is-focused`).waitFor();
    const returned = await snapshot();
    assert.equal(returned.pageLabel, "论文第 1 页");
    assert.equal(returned.activeAnnotationId, assistant.annotation_id);
    assert.equal(returned.cardOpen, "true");
    assert.equal(returned.sourceButtonCount, 1);
    records.push({ zoom, targetPage: 1, targetQuad: quad, sameAnchorSwitchCount: 2, atSource, differentPage, returned, automaticCrossPage: "passed" });
    await page.screenshot({ path: `${process.env.CAELIAE_READ_LAYOUT_OUTPUT}/cross-page-return-${zoom}.png`, fullPage: true });
    await page.getByRole("button", { name: "关闭批注" }).click();
    await page.getByRole("button", { name: "下一页" }).click();
    await page.locator('.pdf-page canvas[aria-label="论文第 2 页"]').waitFor();
  }
  assert.equal((await (await page.request.delete(`${baseURL}/api/annotations/${userId}?session_id=${session}`)).json()).deleted, true);

  const lower = {
    ...assistant, page_number: 2, normalized_quads: [{ x: .2, y: .92, width: .25, height: .03 }],
    note: "isolated lower-page assistant remember", remember: true, idempotency_key: crypto.randomUUID(),
  };
  const lowerResponse = await page.request.post(`${baseURL}/api/annotations`, { data: lower });
  assert(lowerResponse.ok(), `isolated lower annotation: ${lowerResponse.status()} ${await lowerResponse.text()}`);
  const lowerAnnotation = (await lowerResponse.json()).annotation;
  await page.reload();
  await page.locator('.pdf-page canvas').first().waitFor();
  if (await page.locator('.pdf-page canvas[aria-label="论文第 2 页"]').count() === 0) {
    await page.getByRole("button", { name: "下一页" }).click();
  }
  await page.locator('.pdf-page canvas[aria-label="论文第 2 页"]').waitFor();
  for (const zoom of [125, 200]) {
    if (await page.locator('.pdf-page canvas[aria-label="论文第 2 页"]').count() === 0) {
      await page.getByRole("button", { name: "下一页" }).click();
      await page.locator('.pdf-page canvas[aria-label="论文第 2 页"]').waitFor();
    }
    await ensureToolbarExpanded();
    while (await page.locator(".pdf-zoom-controls > span").textContent() !== `${zoom}%`) await page.getByRole("button", { name: "＋" }).click();
    const lowerMark = page.locator(`[data-annotation-id="${lowerAnnotation.annotation_id}"]`).first();
    await lowerMark.waitFor({ timeout: 7000 });
    await lowerMark.locator(".annotation-entry-dot").click();
    await page.getByRole("button", { name: "上一页" }).click();
    await page.locator('.pdf-page canvas[aria-label="论文第 1 页"]').waitFor();
    const lowerDetached = await snapshot();
    assert.equal(lowerDetached.activeAnnotationId, lowerAnnotation.annotation_id);
    assert.equal(lowerDetached.sourceButtonCount, 1);
    await page.getByRole("button", { name: "回到原文" }).click();
    await page.locator('.pdf-page canvas[aria-label="论文第 2 页"]').waitFor();
    await page.locator(`[data-annotation-id="${lowerAnnotation.annotation_id}"].is-focused`).waitFor();
    await page.waitForFunction(() => (document.querySelector(".page-stage")?.scrollTop ?? 0) > 0, null, { timeout: 3000 });
    const lowerReturned = await snapshot();
    assert(lowerReturned.scrollTop > 0, `lower-page focus did not scroll at ${zoom}%: ${JSON.stringify(lowerReturned)}`);
    assert.equal(lowerReturned.activeAnnotationId, lowerAnnotation.annotation_id);
    assert.equal(lowerReturned.cardOpen, "true");
    records.push({ zoom, lowerPage: true, lowerTargetQuad: lower.normalized_quads[0], lowerDetached, lowerReturned });
    await page.getByRole("button", { name: "关闭批注" }).click();
  }
  assert.equal((await (await page.request.delete(`${baseURL}/api/annotations/${lowerAnnotation.annotation_id}?session_id=${session}`)).json()).deleted, false);
  console.log(JSON.stringify({ result: "PASS_AUTOMATIC_CROSS_PAGE", records }, null, 2));
} finally { await browser.close(); }
