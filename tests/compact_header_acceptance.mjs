import { mkdir } from "node:fs/promises";
import { chromium } from "../web/node_modules/playwright-core/index.mjs";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const baseURL = requireAcceptanceBaseURL();
const browserPath = process.env.CAELIAE_READ_BROWSER_PATH
  ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const outputDir = process.env.CAELIAE_READ_HEADER_OUTPUT ?? ".artifacts/compact-header";
await mkdir(outputDir, { recursive: true });

const browser = await chromium.launch({ headless: true, executablePath: browserPath });
try {
  const page = await browser.newPage({ viewport: { width: 1180, height: 900 } });
  const { papers } = await (await page.request.get(`${baseURL}/api/papers`)).json();
  let target;
  for (const paper of papers) {
    const { annotations } = await (await page.request.get(`${baseURL}/api/annotations?paper_id=${paper.paper_id}`)).json();
    if (annotations?.length) { target = paper; break; }
  }
  if (!target) throw new Error("no real paper with annotations found");

  await page.goto(`${baseURL}/reader/`);
  await page.locator(".paper-card", { hasText: target.original_filename }).click();
  await page.locator(".annotation-mark").first().waitFor();
  await page.getByRole("button", { name: "＋" }).click();

  const metrics = () => page.evaluate(() => {
    const app = document.querySelector(".app-shell");
    const header = document.querySelector(".topbar");
    const workspace = document.querySelector(".workspace");
    const toolbar = document.querySelector(".compact-toolbar, .desktop-toolbar");
    const stage = document.querySelector(".page-stage");
    const headerRect = header.getBoundingClientRect();
    const workspaceRect = workspace.getBoundingClientRect();
    return {
      viewportWidth: innerWidth,
      compactLayout: workspace.dataset.layout === "compact",
      headerCompact: app.dataset.headerCompact === "true",
      headerHeight: headerRect.height,
      headerMarginBottom: parseFloat(getComputedStyle(header).marginBottom),
      headerWorkspaceGap: workspaceRect.top - headerRect.bottom,
      toolbarHeight: toolbar.getBoundingClientRect().height,
      toolbarSlim: toolbar.classList.contains("slim"),
      scrollTop: stage.scrollTop,
      blocker: workspace.dataset.scrollBlocker || null,
      pageLabel: document.querySelector(".pdf-page canvas")?.getAttribute("aria-label"),
      zoomLabel: document.querySelector(".pdf-zoom-controls > span")?.textContent,
      canvasWidth: document.querySelector(".pdf-page canvas")?.getBoundingClientRect().width,
      annotationCount: document.querySelectorAll(".annotation-mark").length,
      selectionText: document.getSelection()?.toString() ?? "",
    };
  });
  const wheel = async (deltaY) => {
    const box = await page.locator(".page-stage").boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + Math.min(180, box.height / 2));
    for (let left = Math.abs(deltaY); left > 0; left -= 40) {
      await page.mouse.wheel(0, Math.sign(deltaY) * Math.min(40, left));
      await page.waitForTimeout(35);
    }
    await page.waitForTimeout(220);
  };
  const resetTop = async () => { await page.locator(".page-stage").evaluate((node) => node.scrollTo(0, 0)); await page.waitForTimeout(220); };

  await page.setViewportSize({ width: 800, height: 1024 });
  await page.locator(".compact-toolbar").waitFor();
  await resetTop();
  const mediumFull = await metrics();
  await wheel(200);
  const mediumReading = await metrics();
  if (!mediumReading.headerCompact || !mediumReading.toolbarSlim || mediumReading.headerHeight < 44 || mediumReading.headerHeight > 52 || mediumReading.toolbarHeight > 31) throw new Error("medium compact reading chrome failed");
  if (mediumReading.headerWorkspaceGap > 5) throw new Error("compact header left excess space above workspace");
  await page.screenshot({ path: `${outputDir}/01-medium-reading.png`, fullPage: true });
  await wheel(-160);
  const mediumRestored = await metrics();
  if (mediumRestored.headerCompact || mediumRestored.toolbarSlim) throw new Error("upward hysteresis did not restore medium header and toolbar");

  await resetTop();
  await page.getByRole("button", { name: /^论文/ }).click();
  await page.waitForTimeout(300);
  await page.locator(".page-stage").evaluate((node) => node.scrollBy(0, 140));
  await page.waitForTimeout(100);
  const drawerGuarded = await metrics();
  if (drawerGuarded.headerCompact || drawerGuarded.blocker !== "drawer") throw new Error("drawer did not guard header transition");
  await page.getByRole("button", { name: "关闭论文列表" }).click();

  await resetTop();
  await page.locator(".topbar").dispatchEvent("pointerdown");
  await page.locator(".page-stage").evaluate((node) => node.scrollBy(0, 140));
  await page.waitForTimeout(100);
  const headerPointerGuarded = await metrics();
  if (headerPointerGuarded.headerCompact || headerPointerGuarded.blocker !== "header-operation") throw new Error("header pointer operation did not guard transition");
  await page.locator(".topbar").dispatchEvent("pointercancel");
  await page.waitForTimeout(300);

  await resetTop();
  await page.locator(".annotation-mark .annotation-entry-dot").first().click();
  await page.waitForTimeout(300);
  await page.locator(".page-stage").evaluate((node) => node.scrollBy(0, 140));
  await page.waitForTimeout(100);
  const cardGuarded = await metrics();
  if (cardGuarded.headerCompact || cardGuarded.blocker !== "annotation-card") throw new Error("annotation card did not guard header transition");
  await page.getByRole("button", { name: "关闭批注" }).click();

  await resetTop();
  await page.locator(".text-layer").evaluate((layer) => {
    const target = layer.querySelector("span");
    target.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
    const range = document.createRange(); range.selectNodeContents(target);
    const selection = document.getSelection(); selection.removeAllRanges(); selection.addRange(range);
  });
  await page.locator(".page-stage").evaluate((node) => node.scrollBy(0, 140));
  await page.waitForTimeout(100);
  const selectionGuarded = await metrics();
  if (selectionGuarded.headerCompact || selectionGuarded.blocker !== "text-selection") throw new Error("text selection did not guard header transition");
  await page.locator(".text-layer").dispatchEvent("pointercancel");
  await page.waitForTimeout(450);

  await resetTop();
  await wheel(200); await wheel(-160); await wheel(200);
  const continuous = await metrics();
  if (!continuous.headerCompact || !continuous.toolbarSlim) throw new Error("continuous direction changes destabilized compact chrome");

  const stateBeforeWidthChanges = await metrics();
  await page.setViewportSize({ width: 1180, height: 800 });
  await page.waitForTimeout(250);
  const sidebarClosed = await metrics();
  await page.setViewportSize({ width: 800, height: 1024 });
  await page.waitForTimeout(250);
  const sidebarOpen = await metrics();
  if (sidebarClosed.headerCompact || sidebarClosed.compactLayout || !sidebarOpen.compactLayout) throw new Error("content width switch produced the wrong header/layout mode");
  if (stateBeforeWidthChanges.pageLabel !== sidebarOpen.pageLabel || stateBeforeWidthChanges.canvasWidth !== sidebarOpen.canvasWidth || sidebarOpen.annotationCount < 1) throw new Error("reader state changed across content width switch");

  await page.setViewportSize({ width: 430, height: 900 });
  await page.waitForTimeout(250); await resetTop();
  const narrowFull = await metrics();
  await wheel(200);
  const narrowReading = await metrics();
  if (!narrowReading.headerCompact || !narrowReading.toolbarSlim || narrowReading.headerHeight < 44 || narrowReading.headerHeight > 52) throw new Error("narrow compact reading chrome failed");
  await page.screenshot({ path: `${outputDir}/02-narrow-reading.png`, fullPage: true });

  console.log(JSON.stringify({ paper: target.original_filename, mediumFull, mediumReading, mediumRestored, drawerGuarded, headerPointerGuarded, cardGuarded, selectionGuarded, continuous, sidebarClosed, sidebarOpen, narrowFull, narrowReading }, null, 2));
} finally {
  await browser.close();
}
