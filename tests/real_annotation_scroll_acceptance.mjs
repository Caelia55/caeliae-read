import { mkdir } from "node:fs/promises";
import { chromium } from "../web/node_modules/playwright-core/index.mjs";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const baseURL = requireAcceptanceBaseURL();
const browserPath = process.env.CAELIAE_READ_BROWSER_PATH
  ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const outputDir = process.env.CAELIAE_READ_SCROLL_OUTPUT ?? ".artifacts/real-annotation-scroll";

await mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: browserPath });

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const papersResponse = await page.request.get(`${baseURL}/api/papers`);
  const { papers } = await papersResponse.json();
  let target;
  for (const paper of papers) {
    const response = await page.request.get(`${baseURL}/api/annotations?paper_id=${paper.paper_id}`);
    const body = await response.json();
    if (body.annotations?.length) { target = { paper, annotations: body.annotations }; break; }
  }
  if (!target) throw new Error("no real paper with an existing annotation was found");

  await page.goto(`${baseURL}/reader/`);
  await page.locator(".paper-card", { hasText: target.paper.original_filename }).click();
  await page.locator(".annotation-mark").first().waitFor();
  await page.setViewportSize({ width: 800, height: 1024 });
  await page.locator(".compact-toolbar").waitFor();
  await page.waitForTimeout(150);

  await page.evaluate(() => {
    window.__caeliaeReadAcceptanceScrollLog = [];
    const stage = document.querySelector(".page-stage");
    let previous = stage.scrollTop;
    stage.addEventListener("scroll", () => {
      const workspace = document.querySelector(".workspace");
      const toolbar = document.querySelector(".compact-toolbar");
      const selection = document.getSelection();
      const current = stage.scrollTop;
      window.__caeliaeReadAcceptanceScrollLog.push({
        scrollTop: current,
        delta: current - previous,
        accumulatedDown: Number(workspace.dataset.scrollDown || 0),
        compact: workspace.dataset.layout === "compact",
        slim: toolbar?.dataset.slim === "true",
        drawerOpen: workspace.dataset.drawerOpen === "true",
        annotationCardOpen: workspace.dataset.annotationCardOpen === "true",
        activeAnnotationId: workspace.dataset.activeAnnotationId || null,
        selectionCollapsed: selection?.isCollapsed ?? true,
        toolbarHasFocus: toolbar?.contains(document.activeElement) ?? false,
        blockedBy: workspace.dataset.scrollBlocker || null,
      });
      previous = current;
    }, { passive: true });
  });

  const snapshot = () => page.evaluate(() => {
    const stage = document.querySelector(".page-stage");
    const toolbar = document.querySelector(".compact-toolbar");
    const workspace = document.querySelector(".workspace");
    const selection = document.getSelection();
    const toolbarRect = toolbar.getBoundingClientRect();
    const stageRect = stage.getBoundingClientRect();
    return {
      scrollTop: stage.scrollTop,
      compact: workspace.dataset.layout === "compact",
      slim: toolbar.dataset.slim === "true",
      toolbarHeight: toolbarRect.height,
      toolbarBottom: toolbarRect.bottom,
      stageTop: stageRect.top,
      overlapsPdfViewport: toolbarRect.bottom > stageRect.top + 1,
      drawerOpen: workspace.dataset.drawerOpen === "true",
      annotationCardOpen: workspace.dataset.annotationCardOpen === "true",
      activeAnnotationId: workspace.dataset.activeAnnotationId || null,
      selectionCollapsed: selection?.isCollapsed ?? true,
      selectionText: selection?.toString() ?? "",
      toolbarHasFocus: toolbar.contains(document.activeElement),
      blockedBy: workspace.dataset.scrollBlocker || null,
      annotationCount: document.querySelectorAll(".annotation-mark").length,
    };
  });
  const wheel = async (deltaY) => {
    const box = await page.locator(".page-stage").boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + Math.min(180, box.height / 2));
    for (let remaining = Math.abs(deltaY); remaining > 0; remaining -= 40) {
      await page.mouse.wheel(0, Math.sign(deltaY) * Math.min(40, remaining));
      await page.waitForTimeout(35);
    }
    await page.waitForTimeout(200);
  };

  await page.locator(".page-stage").evaluate((node) => node.scrollTo(0, 0));
  await wheel(200);
  const ordinaryAnnotation = await snapshot();
  if (!ordinaryAnnotation.slim || ordinaryAnnotation.annotationCount < 1) throw new Error("existing annotations blocked normal collapse");
  await page.screenshot({ path: `${outputDir}/01-existing-annotation-slim.png`, fullPage: true });

  await wheel(-120);
  const restoredUp = await snapshot();
  if (restoredUp.slim) throw new Error("upward wheel did not expand toolbar");
  await wheel(200);
  await page.getByRole("button", { name: "展开阅读工具栏" }).click();
  const restoredByButton = await snapshot();
  if (restoredByButton.slim) throw new Error("expand button did not restore toolbar");

  await page.locator(".annotation-mark .annotation-entry-dot").first().click();
  const cardOpen = await snapshot();
  if (!cardOpen.annotationCardOpen || cardOpen.slim) throw new Error("annotation card did not pin expanded toolbar");
  await page.waitForTimeout(300);
  await page.locator(".page-stage").evaluate((node) => node.scrollBy(0, 140));
  await page.waitForTimeout(100);
  const cardGuarded = await snapshot();
  if (cardGuarded.blockedBy !== "annotation-card" || cardGuarded.slim) throw new Error("visible annotation card was not the explicit blocker");
  await page.getByRole("button", { name: "关闭批注" }).click();
  const activeAfterClose = (await snapshot()).activeAnnotationId;
  await page.locator(".page-stage").evaluate((node) => node.scrollTo(0, 0));
  await page.waitForTimeout(100);
  await wheel(200);
  const afterCardClose = await snapshot();
  if (!afterCardClose.slim || afterCardClose.annotationCardOpen || afterCardClose.activeAnnotationId !== activeAfterClose) throw new Error("closed card or retained active annotation blocked collapse");

  await wheel(-160);
  await page.locator(".text-layer").evaluate((layer) => {
    const target = layer.querySelector("span");
    target.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
    const range = document.createRange();
    range.selectNodeContents(target);
    const selection = document.getSelection();
    selection.removeAllRanges(); selection.addRange(range);
  });
  await page.locator(".page-stage").evaluate((node) => node.scrollBy(0, 120));
  await page.waitForTimeout(100);
  const selectionGuarded = await snapshot();
  if (selectionGuarded.blockedBy !== "text-selection" || selectionGuarded.slim) throw new Error("active text drag did not pin the toolbar");
  await page.locator(".text-layer").evaluate((layer) => layer.dispatchEvent(new PointerEvent("pointercancel", { bubbles: true })));
  await page.waitForTimeout(450);
  await page.locator(".page-stage").evaluate((node) => node.scrollTo(0, 0));
  await page.waitForTimeout(100);
  const retainedSelection = await snapshot();
  if (retainedSelection.selectionCollapsed || !retainedSelection.selectionText) throw new Error("test did not retain a browser Selection");
  await wheel(200);
  const afterSelectionSettled = await snapshot();
  if (!afterSelectionSettled.slim || afterSelectionSettled.selectionCollapsed) throw new Error("settled retained Selection permanently blocked collapse");

  await page.waitForTimeout(6500);
  const afterPolling = await snapshot();
  if (!afterPolling.slim) throw new Error("annotation polling reset the slim state");
  await page.screenshot({ path: `${outputDir}/02-after-card-selection-polling.png`, fullPage: true });

  await page.setViewportSize({ width: 1180, height: 800 });
  await page.waitForTimeout(200);
  await page.setViewportSize({ width: 800, height: 1024 });
  await page.locator(".compact-toolbar").waitFor();
  await page.evaluate(() => document.getSelection()?.removeAllRanges());
  await page.locator(".page-stage").evaluate((node) => node.scrollTo(0, 0));
  await wheel(200);
  const afterModeReplacement = await snapshot();
  if (!afterModeReplacement.slim) throw new Error("listener was not attached to the current compact page-stage");

  const log = await page.evaluate(() => window.__caeliaeReadAcceptanceScrollLog);
  console.log(JSON.stringify({
    paper: { paper_id: target.paper.paper_id, original_filename: target.paper.original_filename, annotation_count: target.annotations.length },
    ordinaryAnnotation, restoredUp, restoredByButton, cardOpen, cardGuarded, afterCardClose, selectionGuarded,
    retainedSelection, afterSelectionSettled, afterPolling, afterModeReplacement,
    scrollLog: log,
  }, null, 2));
} finally {
  await browser.close();
}
