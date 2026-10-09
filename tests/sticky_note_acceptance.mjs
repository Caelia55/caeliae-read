import assert from "node:assert/strict";
import { chromium } from "../web/node_modules/playwright-core/index.mjs";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const baseURL = requireAcceptanceBaseURL();
const browser = await chromium.launch({ headless: true, executablePath: process.env.CAELIAE_READ_BROWSER_PATH ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(`${baseURL}/reader/`);
  await page.locator(".paper-card").first().click();
  await page.locator(".pdf-page canvas").waitFor();
  await page.locator(".sync-saved").waitFor();
  const paperId = await page.evaluate(() => localStorage.getItem("caeliae.read.activePaperId"));

  const stickyTool = page.locator(".sticky-tool:visible");
  await stickyTool.click();
  await assert.equal(await stickyTool.getAttribute("aria-pressed"), "true");
  await page.locator(".sticky-placement-hint").waitFor();
  const textLayer = page.locator(".text-layer");
  const textLayerBox = await textLayer.boundingBox();
  assert(textLayerBox && textLayerBox.width > 0 && textLayerBox.height > 0, "text layer is not measurable");
  await textLayer.click({ position: { x: textLayerBox.width - 24, y: textLayerBox.height - 24 } });
  await page.getByRole("textbox", { name: "便签内容" }).waitFor();
  await page.getByRole("textbox", { name: "便签内容" }).fill("ui placed sticky");
  await page.getByRole("button", { name: "保存便签" }).click();
  const uiMarker = page.locator('.sticky-note-marker[aria-label="便签：ui placed sticky"]');
  await uiMarker.waitFor();
  await page.waitForFunction(() => {
    return !document.querySelector(".sticky-placement-mode") && !document.querySelector(".sticky-placement-hint");
  }, undefined, { timeout: 5_000 });
  assert.equal(await page.locator(".sticky-placement-hint").count(), 0, "placement hint remained after saving");

  const normalizedMarkerPoint = async () => uiMarker.evaluate((node) => {
    const pageRect = document.querySelector(".pdf-page").getBoundingClientRect();
    const rect = node.getBoundingClientRect();
    return { x: (rect.left + rect.width / 2 - pageRect.left) / pageRect.width, y: (rect.top + rect.height / 2 - pageRect.top) / pageRect.height };
  });
  const beforeJitter = await normalizedMarkerPoint();
  const jitterBox = await uiMarker.boundingBox();
  assert(jitterBox, "sticky marker is not measurable before jitter");
  await page.mouse.move(jitterBox.x + jitterBox.width / 2, jitterBox.y + jitterBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(jitterBox.x + jitterBox.width / 2 + 2, jitterBox.y + jitterBox.height / 2 + 2);
  await page.mouse.up();
  await page.locator(".sticky-composer").waitFor();
  const afterJitter = await normalizedMarkerPoint();
  assert(Math.abs(afterJitter.x - beforeJitter.x) < .01 && Math.abs(afterJitter.y - beforeJitter.y) < .01, "jitter moved sticky marker");
  await page.getByRole("button", { name: "关闭便签编辑器" }).click();

  const pageRect = await page.locator(".pdf-page").evaluate((node) => { const rect = node.getBoundingClientRect(); return { left: rect.left, top: rect.top, width: rect.width, height: rect.height }; });
  const markerBox = await uiMarker.boundingBox();
  assert(markerBox, "sticky marker is not measurable before drag");
  await page.mouse.move(markerBox.x + markerBox.width / 2, markerBox.y + markerBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(pageRect.left + pageRect.width * .62, pageRect.top + pageRect.height * .58);
  await page.mouse.up();
  await page.getByText("便签位置已保存").waitFor();
  const moved = await page.request.get(`${baseURL}/api/sticky-notes`, { params: { paper_id: paperId, page: 1 } });
  const movedNote = (await moved.json()).sticky_notes.find((item) => item.text === "ui placed sticky");
  assert(movedNote && Math.abs(movedNote.x - .62) < .03 && Math.abs(movedNote.y - .58) < .03, "drag did not persist normalized point");

  const clampPageRect = await page.locator(".pdf-page").evaluate((node) => { const rect = node.getBoundingClientRect(); return { left: rect.left, top: rect.top }; });
  const clampMarkerBox = await uiMarker.boundingBox();
  assert(clampMarkerBox, "sticky marker is not measurable before clamp");
  await page.mouse.move(clampMarkerBox.x + clampMarkerBox.width / 2, clampMarkerBox.y + clampMarkerBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(clampPageRect.left + 1, clampPageRect.top + 1);
  await page.mouse.up();
  await page.waitForTimeout(180);
  const clamped = await page.request.get(`${baseURL}/api/sticky-notes`, { params: { paper_id: paperId, page: 1 } });
  const clampedNote = (await clamped.json()).sticky_notes.find((item) => item.text === "ui placed sticky");
  assert(clampedNote && clampedNote.x < .03 && clampedNote.y < .03, "drag to page boundary did not clamp to the page edge");
  await page.request.put(`${baseURL}/api/sticky-notes/${clampedNote.id}`, { data: { x: movedNote.x, y: movedNote.y, text: clampedNote.text, style_key: clampedNote.style_key } });

  await page.reload();
  await uiMarker.waitFor();
  const restored = await normalizedMarkerPoint();
  assert(Math.abs(restored.x - movedNote.x) < .03 && Math.abs(restored.y - movedNote.y) < .03, "dragged point drifted after refresh");

  await uiMarker.click();
  await page.locator(".sticky-composer").waitFor();
  const resizeHandle = page.locator('[data-overlay-resize-handle="sticky"]');
  const resizeBox = await resizeHandle.boundingBox();
  const resizeBefore = await page.locator(".sticky-composer").boundingBox();
  assert(resizeBox && resizeBefore, "sticky resize handle/card is not measurable");
  await page.mouse.move(resizeBox.x + resizeBox.width / 2, resizeBox.y + resizeBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(resizeBox.x + resizeBox.width / 2 + 42, resizeBox.y + resizeBox.height / 2 + 32);
  await page.mouse.up();
  const resizeAfter = await page.locator(".sticky-composer").boundingBox();
  assert(resizeAfter && resizeAfter.width > resizeBefore.width + 20 && resizeAfter.height > resizeBefore.height + 10, "sticky resize handle did not change card size");
  await page.getByRole("button", { name: "关闭便签编辑器" }).click();
  await page.reload();
  await uiMarker.waitFor();
  await uiMarker.click();
  await page.locator(".sticky-composer").waitFor();
  const persistedResize = await page.locator(".sticky-composer").boundingBox();
  assert(persistedResize && persistedResize.width > resizeBefore.width + 20 && persistedResize.height > resizeBefore.height + 10, "sticky explicit size did not persist after refresh");
  await page.getByRole("button", { name: "恢复默认大小" }).click();
  const resetResize = await page.locator(".sticky-composer").boundingBox();
  assert(resetResize && resetResize.width < persistedResize.width && resetResize.height < persistedResize.height, "sticky reset did not restore responsive size");
  await page.getByRole("button", { name: "关闭便签编辑器" }).click();

  const cancelBox = await uiMarker.boundingBox();
  assert(cancelBox, "sticky marker is not measurable before pointercancel");
  await page.mouse.move(cancelBox.x + cancelBox.width / 2, cancelBox.y + cancelBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(cancelBox.x + cancelBox.width / 2 + 40, cancelBox.y + cancelBox.height / 2 + 40);
  await page.evaluate(() => {
    const marker = document.querySelector('.sticky-note-marker[aria-label="便签：ui placed sticky"]');
    marker?.dispatchEvent(new PointerEvent("pointercancel", { bubbles: true, pointerId: 1 }));
  });
  await page.mouse.up();
  if (await page.locator(".sticky-composer").count()) await page.getByRole("button", { name: "关闭便签编辑器" }).click();
  const afterCancel = await normalizedMarkerPoint();
  assert(Math.abs(afterCancel.x - movedNote.x) < .03 && Math.abs(afterCancel.y - movedNote.y) < .03, "pointercancel did not restore sticky point");
  const persistedAfterCancel = await page.request.get(`${baseURL}/api/sticky-notes`, { params: { paper_id: paperId, page: 1 } });
  const cancelNote = (await persistedAfterCancel.json()).sticky_notes.find((item) => item.text === "ui placed sticky");
  assert(cancelNote && Math.abs(cancelNote.x - movedNote.x) < .03 && Math.abs(cancelNote.y - movedNote.y) < .03, "pointercancel unexpectedly persisted a new point");

  const revealHandle = page.locator(".toolbar-reveal-handle:visible");
  if (await revealHandle.count()) await revealHandle.click();
  const zoomMinus = page.locator(".pdf-zoom-controls button").nth(0);
  const zoomPlus = page.locator(".pdf-zoom-controls button").nth(1);
  await zoomPlus.click();
  await zoomMinus.click();
  for (let index = 0; index < 5; index += 1) {
    if (index > 0) await zoomPlus.click();
    await page.waitForTimeout(80);
    const zoomPoint = await normalizedMarkerPoint();
    assert(Math.abs(zoomPoint.x - movedNote.x) < .03 && Math.abs(zoomPoint.y - movedNote.y) < .03, `normalized point drifted at zoom step ${index}`);
  }
  await page.setViewportSize({ width: 1000, height: 800 });
  await page.waitForTimeout(120);
  const resizedPoint = await normalizedMarkerPoint();
  assert(Math.abs(resizedPoint.x - movedNote.x) < .03 && Math.abs(resizedPoint.y - movedNote.y) < .03, "normalized point drifted after resize");

  const createdResponse = await page.request.post(`${baseURL}/api/sticky-notes`, { data: { paper_id: paperId, page: 1, x: .9, y: .45, text: "isolated sticky note", style_key: "tertiary" } });
  assert(createdResponse.ok());
  const created = (await createdResponse.json()).sticky_note;
  await page.reload();
  const marker = page.locator(`.sticky-note-marker[aria-label="便签：${created.text}"]`);
  await marker.waitFor({ timeout: 7000 });
  const rendered = await marker.evaluate((node) => {
    const pageRect = document.querySelector(".pdf-page").getBoundingClientRect();
    const rect = node.getBoundingClientRect();
    return { x: (rect.left + rect.width / 2 - pageRect.left) / pageRect.width, y: (rect.top + rect.height / 2 - pageRect.top) / pageRect.height, className: node.className };
  });
  assert(Math.abs(rendered.x - .9) < .03 && Math.abs(rendered.y - .45) < .03, "sticky marker drifted from normalized point");
  await marker.click();
  await page.locator(".sticky-composer").waitFor();
  await page.getByLabel("便签内容").fill("edited sticky note");
  await page.getByRole("button", { name: "保存便签" }).click();
  const editedMarker = page.locator('.sticky-note-marker[aria-label="便签：edited sticky note"]');
  await editedMarker.waitFor();
  await editedMarker.click();
  await page.locator(".sticky-composer").waitFor();
  await page.locator(".sticky-composer").getByRole("button", { name: "删除" }).click();
  await page.request.delete(`${baseURL}/api/sticky-notes/${movedNote.id}`);
  const remaining = await page.request.get(`${baseURL}/api/sticky-notes`, { params: { paper_id: paperId } });
  assert.equal((await remaining.json()).sticky_notes.length, 0);
  console.log(JSON.stringify({
    placementModeBlankTextLayer: true,
    jitterStable: true,
    dragPersisted: { x: movedNote.x, y: movedNote.y },
    refreshStable: true,
    pointerCancelRestored: Boolean(cancelNote && Math.abs(cancelNote.x - movedNote.x) < .03 && Math.abs(cancelNote.y - movedNote.y) < .03),
    zoomStable: true,
    resizeStable: true,
    edgeClamp: { x: clampedNote.x, y: clampedNote.y },
    styleKey: "tertiary",
    edit: true,
    delete: true,
  }));
} finally { await browser.close(); }
