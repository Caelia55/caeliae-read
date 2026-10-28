import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { chromium } from "../web/node_modules/playwright-core/index.mjs";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const baseURL = requireAcceptanceBaseURL();
const pdfPath = process.env.CAELIAE_READ_ACCEPTANCE_PDF;
if (!pdfPath) throw new Error("CAELIAE_READ_ACCEPTANCE_PDF is required");
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CAELIAE_READ_BROWSER_PATH ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
});

const pdf = await readFile(pdfPath);
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function assertClose(actual, expected, message) {
  assert(Math.abs(actual - expected) <= 1, `${message}: ${actual} vs ${expected}`);
}

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.addInitScript(() => {
    localStorage.setItem("caeliae.read.papersPanelOpen", "false");
    localStorage.setItem("caeliae.read.annotationsPanelOpen", "false");
  });
  for (let index = 0; index < 12; index += 1) {
    const upload = await page.request.post(`${baseURL}/api/papers`, {
      multipart: {
        file: {
          name: `drawer-layout-${index}.pdf`,
          mimeType: "application/pdf",
          buffer: Buffer.concat([pdf, Buffer.from(`\n% drawer layout fixture ${index}\n`)]),
        },
      },
    });
    assert([200, 201].includes(upload.status()), `paper fixture upload failed: ${upload.status()}`);
  }

  await page.goto(`${baseURL}/reader/`);
  const papersToggle = page.getByRole("button", { name: "论文", exact: true });
  await papersToggle.click();
  await page.locator(".library").waitFor();
  await page.locator(".library .paper-card").first().click();
  await page.locator(".pdf-page canvas").waitFor();
  await page.locator(".sync-saved").waitFor();
  await page.locator(".library .side-panel-close").click();

  const readLayout = (label) => page.evaluate((label) => {
    const read = (selector) => {
      const node = document.querySelector(selector);
      if (!node) return null;
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      return {
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
        position: style.position,
        display: style.display,
        zIndex: style.zIndex,
        overflowY: style.overflowY,
        scrollHeight: node.scrollHeight,
        clientHeight: node.clientHeight,
      };
    };
    const workspace = document.querySelector(".workspace");
    return {
      label,
      workspace: read(".workspace"),
      reader: read(".reader"),
      stage: read(".page-stage"),
      canvas: read(".pdf-page canvas"),
      library: read(".library"),
      paperList: read(".library > .paper-list"),
      paperDrawer: read(".paper-drawer"),
      gridColumns: workspace ? getComputedStyle(workspace).gridTemplateColumns : null,
      papersOpen: workspace?.dataset.papersOpen,
      horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    };
  }, label);

  const closed = await readLayout("closed");
  assert.equal(closed.papersOpen, "false");
  assert.equal(closed.library.display, "none");
  await papersToggle.click();
  await page.locator(".library").waitFor();
  const papersOpen = await readLayout("papers");
  assert.equal(papersOpen.library.position, "absolute");
  assert.notEqual(papersOpen.library.display, "none");
  assert.equal(papersOpen.paperList.scrollHeight > papersOpen.paperList.clientHeight, true, "long paper list did not scroll inside the drawer");
  assert.equal(papersOpen.reader.x, closed.reader.x, "opening Papers pushed the Reader right");
  assertClose(papersOpen.reader.width, closed.reader.width, "opening Papers reflowed Reader width");
  assertClose(papersOpen.stage.width, closed.stage.width, "opening Papers reflowed PDF stage width");
  assertClose(papersOpen.canvas.width, closed.canvas.width, "opening Papers reflowed PDF canvas width");
  assert.equal(papersOpen.gridColumns, closed.gridColumns, "opening Papers changed workspace grid columns");
  assert.equal(papersOpen.horizontalOverflow, false);

  const shellBeforeMetadata = await page.locator(".library").boundingBox();
  await page.getByTestId("paper-metadata-open").click();
  await page.getByTestId("paper-metadata-panel").waitFor();
  await page.locator('[data-testid="paper-metadata-empty"], [data-testid="paper-metadata-saved"]').waitFor();
  assert.equal(await page.getByTestId("paper-list-view").count(), 0, "Metadata view retained the Papers list");
  assert.equal(await page.getByTestId("paper-metadata-empty").count(), 1);
  const metadata = await readLayout("metadata");
  const shellAfterMetadata = await page.locator(".library").boundingBox();
  assertClose(shellAfterMetadata.width, shellBeforeMetadata.width, "Metadata changed drawer width");
  assertClose(shellAfterMetadata.x, shellBeforeMetadata.x, "Metadata changed drawer position");
  assertClose(metadata.stage.width, closed.stage.width, "Metadata reflowed PDF stage width");
  await page.getByRole("button", { name: "返回" }).click();
  await page.getByTestId("paper-list-view").waitFor();
  const afterBack = await readLayout("back");
  assertClose(afterBack.stage.width, closed.stage.width, "Back to Papers reflowed PDF stage width");

  await page.getByTestId("paper-metadata-open").click();
  await page.getByTestId("paper-metadata-panel").waitFor();
  await page.getByTestId("paper-metadata-edit").click();
  const edit = await readLayout("edit");
  assertClose(edit.stage.width, closed.stage.width, "Metadata edit reflowed PDF stage width");
  await page.keyboard.press("Escape");
  await page.getByTestId("paper-metadata-view").waitFor();
  await page.getByRole("button", { name: "关闭论文面板" }).click();
  await wait(100);
  const afterClose = await readLayout("after-close");
  assert.equal(afterClose.library.display, "none");
  assertClose(afterClose.stage.width, closed.stage.width, "Closing Papers did not restore PDF stage width");
  assertClose(afterClose.canvas.width, closed.canvas.width, "Closing Papers did not restore PDF canvas width");

  await page.setViewportSize({ width: 390, height: 844 });
  await wait(200);
  const narrowClosed = await readLayout("narrow-closed");
  await page.getByRole("button", { name: /^论文/ }).click();
  await page.locator(".paper-drawer").waitFor();
  const narrowPapers = await readLayout("narrow-papers");
  assert.equal(narrowPapers.paperDrawer.position, "absolute");
  assertClose(narrowPapers.reader.width, narrowClosed.reader.width, "narrow Papers reflowed Reader width");
  assert.equal(narrowPapers.horizontalOverflow, false);
  await page.locator(".paper-metadata-drawer-trigger").click();
  await page.locator(".paper-drawer .paper-metadata-panel").waitFor();
  const narrowMetadata = await readLayout("narrow-metadata");
  assertClose(narrowMetadata.reader.width, narrowClosed.reader.width, "narrow Metadata reflowed Reader width");
  assert.equal(narrowMetadata.horizontalOverflow, false);
  await page.getByRole("button", { name: "返回" }).click();
  await page.locator(".paper-drawer [data-testid=paper-list-view]").waitFor();
  await page.getByRole("button", { name: "关闭论文列表" }).click();
  await wait(100);
  const narrowAfterClose = await readLayout("narrow-after-close");
  assert(narrowAfterClose.paperDrawer === null || narrowAfterClose.paperDrawer.display === "none", "narrow Papers drawer remained visible after close");
  assert.equal(narrowAfterClose.horizontalOverflow, false);

  console.log(JSON.stringify({
    desktopOverlay: true,
    desktopPdfBoundsStable: true,
    metadataSharesDrawer: true,
    internalPaperListScroll: true,
    editOverlay: true,
    narrowOverlay: true,
    noHorizontalOverflow: true,
  }, null, 2));
} finally {
  await browser.close();
}
