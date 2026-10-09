import { mkdir } from "node:fs/promises";
import { chromium } from "../web/node_modules/playwright-core/index.mjs";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const baseURL = requireAcceptanceBaseURL();
const browserPath = process.env.CAELIAE_READ_BROWSER_PATH
  ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const outputDir = process.env.CAELIAE_READ_LAYOUT_OUTPUT ?? ".artifacts/responsive-layout";
const expectFixed = process.env.CAELIAE_READ_EXPECT_FIXED === "1";
const compactViewportWidth = Number(process.env.CAELIAE_READ_COMPACT_VIEWPORT_WIDTH ?? 768);

await mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: browserPath });

function collectLayout() {
  const workspace = document.querySelector(".workspace");
  const library = document.querySelector(".library");
  const compactToolbar = document.querySelector(".compact-toolbar");
  const desktopToolbar = document.querySelector(".desktop-toolbar")
    ?? document.querySelector(".reader-controls:not(.compact-toolbar)");
  const stage = document.querySelector(".page-stage");
  const style = (element) => {
    if (!element) return null;
    const computed = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return {
      display: computed.display,
      position: computed.position,
      gridArea: computed.gridArea,
      width: rect.width,
      height: rect.height,
    };
  };
  const matchedQueries = [];
  for (const sheet of document.styleSheets) {
    let rules;
    try { rules = sheet.cssRules; } catch { continue; }
    for (const rule of rules) {
      if (rule instanceof CSSMediaRule && matchMedia(rule.conditionText).matches) {
        matchedQueries.push(`@media ${rule.conditionText}`);
      }
      if (typeof CSSContainerRule !== "undefined" && rule instanceof CSSContainerRule) {
        matchedQueries.push(`@container ${rule.conditionText} (present)`);
      }
    }
  }
  return {
    innerWidth: window.innerWidth,
    devicePixelRatio: window.devicePixelRatio,
    documentViewportWidth: document.documentElement.clientWidth,
    workspaceWidth: workspace?.getBoundingClientRect().width ?? null,
    compact: workspace?.classList.contains("compact") ?? false,
    workspaceClass: workspace?.className ?? null,
    workspaceDataLayout: workspace?.getAttribute("data-layout") ?? null,
    matchedQueries,
    library: style(library),
    compactToolbar: style(compactToolbar),
    desktopToolbar: style(desktopToolbar),
    scrollElement: stage?.className ?? null,
    scrollTop: stage?.scrollTop ?? null,
    toolbarSlim: compactToolbar?.classList.contains("slim") ?? false,
  };
}

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(`${baseURL}/reader/`);
  if (await page.locator(".pdf-page").count() === 0) {
    const pdfPath = process.env.CAELIAE_READ_ACCEPTANCE_PDF;
    if (!pdfPath) throw new Error("CAELIAE_READ_ACCEPTANCE_PDF is required when no paper is active");
    await page.locator('input[type="file"]').setInputFiles(pdfPath);
  }
  await page.locator(".pdf-page canvas").waitFor();
  await page.setViewportSize({ width: compactViewportWidth, height: 1024 });
  await page.waitForTimeout(250);
  const initial = await page.evaluate(collectLayout);
  await page.screenshot({ path: `${outputDir}/compact-expanded.png`, fullPage: true });

  await page.getByRole("button", { name: "＋" }).click();
  await page.waitForTimeout(250);

  const stateBefore = await page.evaluate(() => {
    const pageLabel = document.querySelector(".compact-toolbar > span:not(.pdf-zoom-controls)")?.textContent
      ?? document.querySelector(".toolbar-navigation > span")?.textContent;
    const canvas = document.querySelector(".pdf-page canvas");
    return { pageLabel, zoomLabel: document.querySelector(".pdf-zoom-controls > span")?.textContent, canvasWidth: canvas?.getBoundingClientRect().width ?? null };
  });
  await page.locator(".page-stage").evaluate((node) => node.scrollTo({ top: 180, behavior: "instant" }));
  await page.waitForTimeout(250);
  const scrolled = await page.evaluate(collectLayout);
  await page.screenshot({ path: `${outputDir}/compact-slim.png`, fullPage: true });
  await page.locator(".page-stage").evaluate((node) => node.scrollTo({ top: 0, behavior: "instant" }));
  await page.waitForTimeout(250);
  const restoredUp = await page.evaluate(collectLayout);

  let drawer = null;
  if (initial.compactToolbar) {
    await page.getByRole("button", { name: /^论文/ }).click();
    drawer = await page.locator(".paper-drawer").evaluate((node) => ({ display: getComputedStyle(node).display, height: node.getBoundingClientRect().height }));
    await page.getByRole("button", { name: "关闭论文列表" }).click();
  }

  await page.locator(".page-stage").evaluate((node) => node.scrollTo({ top: 100, behavior: "instant" }));
  const scrollBeforeResize = await page.locator(".page-stage").evaluate((node) => node.scrollTop);

  await page.setViewportSize({ width: 1180, height: 800 });
  await page.waitForTimeout(250);
  const wide = await page.evaluate(collectLayout);
  const stateAfter = await page.evaluate(() => {
    const pageLabel = document.querySelector(".toolbar-navigation > span")?.textContent
      ?? document.querySelector(".compact-toolbar > span:not(.pdf-zoom-controls)")?.textContent;
    const canvas = document.querySelector(".pdf-page canvas");
    return { pageLabel, zoomLabel: document.querySelector(".pdf-zoom-controls > span")?.textContent, canvasWidth: canvas?.getBoundingClientRect().width ?? null, scrollTop: document.querySelector(".page-stage")?.scrollTop ?? null };
  });
  await page.screenshot({ path: `${outputDir}/desktop-restored.png`, fullPage: true });

  const result = { initial, scrolled, restoredUp, drawer, wide, stateBefore, scrollBeforeResize, stateAfter };
  if (expectFixed) {
    if (!initial.compact || initial.library?.display !== "none" || initial.compactToolbar?.display !== "flex" || initial.desktopToolbar !== null) throw new Error("compact DOM/layout contract failed");
    if (!scrolled.toolbarSlim || scrolled.scrollTop < 96 || scrolled.compactToolbar?.height > 31) throw new Error("compact toolbar did not become slim on the actual scroller");
    if (restoredUp.toolbarSlim) throw new Error("compact toolbar did not expand on upward scroll");
    if (!drawer || drawer.display === "none") throw new Error("paper drawer did not open on demand");
    if (wide.compact || wide.library?.display !== "none" || wide.compactToolbar !== null || wide.desktopToolbar?.display !== "flex") throw new Error("desktop layout or explicit Papers closed state was not restored");
    if (stateBefore.pageLabel !== stateAfter.pageLabel || stateBefore.zoomLabel !== stateAfter.zoomLabel || stateBefore.canvasWidth !== stateAfter.canvasWidth || stateAfter.scrollTop !== scrollBeforeResize) throw new Error("PDF page, zoom, or scroll state changed across layout switches");
  }
  console.log(JSON.stringify(result, null, 2));
} finally {
  await browser.close();
}
