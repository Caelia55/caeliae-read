import assert from "node:assert/strict";
import { chromium } from "../web/node_modules/playwright-core/index.mjs";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const baseURL = requireAcceptanceBaseURL();
const pdfPath = process.env.CAELIAE_READ_ACCEPTANCE_PDF;
if (!pdfPath) throw new Error("CAELIAE_READ_ACCEPTANCE_PDF is required");
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CAELIAE_READ_BROWSER_PATH ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
});

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(`${baseURL}/reader/`);
  await page.locator('input[type="file"]').setInputFiles(pdfPath);
  await page.getByText("1 / 2").waitFor();
  await page.locator(".pdf-page canvas").waitFor();

  const summaryTool = page.getByRole("button", { name: "+ 大意", exact: true }).first();
  await summaryTool.click();
  await page.locator(".summary-placement-hint").waitFor();
  assert.equal(await summaryTool.getAttribute("aria-pressed"), "true");

  const pageBox = await page.locator(".pdf-page").boundingBox();
  assert(pageBox && pageBox.width > 0 && pageBox.height > 0, "PDF page is not measurable");
  const targetY = 0.34;
  await page.mouse.click(pageBox.x + 10, pageBox.y + pageBox.height * targetY);
  await page.getByRole("textbox", { name: "大意内容" }).waitFor();
  await page.getByRole("textbox", { name: "大意内容" }).fill("This paragraph introduces the central mechanism.");
  await page.getByRole("button", { name: "保存大意" }).click();

  const marker = page.locator('.summary-note-marker[aria-label="大意：This paragraph introduces the central mechanism."]');
  await marker.waitFor();
  const paperId = await page.evaluate(() => localStorage.getItem("caeliae.read.activePaperId"));
  assert(paperId, "active paper was not set");
  const listed = await page.request.get(`${baseURL}/api/summary-notes`, { params: { paper_id: paperId } });
  assert(listed.ok());
  const created = (await listed.json()).summary_notes.find((note) => note.text.startsWith("This paragraph"));
  assert(created && created.page_number === 1 && Math.abs(created.normalized_y - targetY) < .03, "summary note did not persist page-relative y");

  await marker.click();
  await page.getByRole("textbox", { name: "大意内容" }).fill("Updated section summary.");
  await page.getByRole("button", { name: "保存大意" }).click();
  const editedMarker = page.locator('.summary-note-marker[aria-label="大意：Updated section summary."]');
  await editedMarker.waitFor();

  await page.getByRole("button", { name: "批注", exact: true }).click();
  await page.locator('.annotation-index-filters button', { hasText: "大意" }).click();
  const indexItem = page.locator(".annotation-index-item").filter({ hasText: "Updated section summary." });
  await indexItem.waitFor();
  await indexItem.click();
  await page.locator('.summary-note-marker.is-index-focused').waitFor();
  assert.equal(await page.locator(".summary-note-composer").count(), 0, "Index jump unexpectedly opened the editor");

  const beforeZoom = await editedMarker.evaluate((node) => {
    const pageRect = document.querySelector(".pdf-page").getBoundingClientRect();
    const rect = node.getBoundingClientRect();
    return (rect.top + rect.height / 2 - pageRect.top) / pageRect.height;
  });
  await page.getByRole("button", { name: "＋" }).click();
  await page.waitForTimeout(150);
  const afterZoom = await editedMarker.evaluate((node) => {
    const pageRect = document.querySelector(".pdf-page").getBoundingClientRect();
    const rect = node.getBoundingClientRect();
    return (rect.top + rect.height / 2 - pageRect.top) / pageRect.height;
  });
  assert(Math.abs(beforeZoom - created.normalized_y) < .05 && Math.abs(afterZoom - created.normalized_y) < .05, "summary marker moved in normalized coordinates after zoom");

  await summaryTool.click();
  await page.locator(".summary-placement-hint").waitFor();
  const firstTextSpan = page.locator(".text-layer span").first();
  await firstTextSpan.click();
  await page.waitForTimeout(150);
  assert.equal(await page.getByRole("textbox", { name: "大意内容" }).count(), 0, "text selection target opened Summary Note editor");
  await page.keyboard.press("Escape");
  assert.equal(await page.locator(".summary-placement-hint").count(), 0, "Escape did not cancel Summary Note placement");

  await editedMarker.click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "删除" }).click();
  await page.waitForFunction(() => !document.querySelector('.summary-note-marker[aria-label="大意：Updated section summary."]'));
  const remaining = await page.request.get(`${baseURL}/api/summary-notes`, { params: { paper_id: paperId } });
  assert.equal((await remaining.json()).summary_notes.length, 0);

  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), false, "Summary Note introduced responsive overflow");
  console.log(JSON.stringify({
    created: true,
    edited: true,
    indexFilterAndJump: true,
    normalizedY: created.normalized_y,
    selectionUnaffected: true,
    zoomStable: true,
    deleted: true,
    responsiveOverflow: false,
  }));
} finally {
  await browser.close();
}
