import assert from "node:assert/strict";
import { chromium } from "../web/node_modules/playwright-core/index.mjs";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const baseURL = requireAcceptanceBaseURL();

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CAELIAE_READ_BROWSER_PATH ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
});

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const papersResponse = await page.request.get(`${baseURL}/api/papers`);
  assert.equal(papersResponse.ok(), true);
  const { papers } = await papersResponse.json();
  assert(papers.length > 0, "a paper is required for the Annotation Index acceptance");
  await page.goto(`${baseURL}/reader/`);
  await page.locator(".paper-card", { hasText: papers[0].original_filename }).click();
  await page.locator(".pdf-page canvas").waitFor();
  await page.locator(".sync-saved").waitFor();
  await page.waitForTimeout(300);

  const papersToggle = page.getByRole("button", { name: "论文", exact: true });
  const annotationsToggle = page.getByRole("button", { name: "批注", exact: true });
  const desktopOrder = await page.locator(".toolbar-navigation > button").evaluateAll((buttons) => buttons.map((button) => button.textContent?.trim()));
  assert.deepEqual(desktopOrder.slice(0, 2), ["论文", "上一页"]);
  const fitClosedWidth = await page.locator(".pdf-page canvas").evaluate((node) => node.getBoundingClientRect().width);
  await annotationsToggle.click();
  await page.locator(".annotation-index").waitFor();
  await page.waitForTimeout(300);
  const fitOpenWidth = await page.locator(".pdf-page canvas").evaluate((node) => node.getBoundingClientRect().width);
  assert(fitOpenWidth < fitClosedWidth, "fit-width did not adapt to the narrower reading track");
  const rowOwnership = await page.evaluate(() => {
    const toolbar = document.querySelector(".desktop-toolbar")?.getBoundingClientRect();
    const index = document.querySelector(".annotation-index")?.getBoundingClientRect();
    return { toolbarBottom: toolbar?.bottom ?? null, indexTop: index?.top ?? null };
  });
  assert(rowOwnership.toolbarBottom !== null && rowOwnership.indexTop !== null && rowOwnership.indexTop >= rowOwnership.toolbarBottom - 1, "Annotation Index overlaps the toolbar row");
  await annotationsToggle.click();
  await page.waitForTimeout(300);
  const fitRestoredWidth = await page.locator(".pdf-page canvas").evaluate((node) => node.getBoundingClientRect().width);
  assert(Math.abs(fitRestoredWidth - fitClosedWidth) <= 1, "fit-width did not restore after closing Annotations");
  await annotationsToggle.click();
  await page.locator(".annotation-index").waitFor();
  assert.equal(await page.locator(".workspace").getAttribute("data-annotations-open"), "true");
  assert.notEqual(await page.locator(".library").evaluate((node) => getComputedStyle(node).display), "none");
  await page.getByRole("button", { name: "荧光", exact: true }).click();
  assert.equal(await page.getByRole("button", { name: "荧光", exact: true }).getAttribute("aria-pressed"), "true");
  await page.getByRole("button", { name: "关闭批注面板" }).click();
  assert.equal(await page.locator(".annotation-index").count(), 0);

  await papersToggle.click();
  assert.equal(await page.locator(".workspace").getAttribute("data-papers-open"), "false");
  assert.equal(await page.locator(".library").evaluate((node) => getComputedStyle(node).display), "none");
  await papersToggle.click();
  assert.equal(await page.locator(".workspace").getAttribute("data-papers-open"), "true");

  await annotationsToggle.click();
  assert.equal(await page.locator(".workspace").getAttribute("data-annotations-open"), "true");
  await page.setViewportSize({ width: 768, height: 900 });
  await page.waitForTimeout(250);
  const compactPapers = page.getByRole("button", { name: /^论文/ });
  const compactAnnotations = page.getByRole("button", { name: "批注", exact: true });
  await compactPapers.click();
  assert.equal(await page.locator(".workspace").getAttribute("data-papers-open"), "true");
  assert.equal(await page.locator(".workspace").getAttribute("data-annotations-open"), "false");
  await page.getByRole("button", { name: "关闭论文列表" }).click();
  await compactAnnotations.click();
  assert.equal(await page.locator(".workspace").getAttribute("data-papers-open"), "false");
  assert.equal(await page.locator(".workspace").getAttribute("data-annotations-open"), "true");

  console.log(JSON.stringify({ desktopToggle: true, filters: true, explicitClose: true, compactMutualExclusion: true }));
} finally {
  await browser.close();
}
