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
const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const paperCard = (page, filename) => page.getByRole("button", { name: new RegExp(`^${escapeRegExp(filename)}\\s`) });

async function openMetadata(page) {
  const trigger = page.getByTestId("paper-metadata-open");
  if (await trigger.isVisible()) {
    await trigger.click();
    return;
  }
  const desktopPapers = page.locator("nav.desktop-toolbar").getByRole("button", { name: "论文", exact: true });
  if (await desktopPapers.isVisible()) {
    await desktopPapers.click();
    await trigger.click();
    return;
  }
  await page.locator("nav.compact-toolbar").getByRole("button", { name: /^论文/ }).click();
  await page.locator(".paper-metadata-drawer-trigger").click();
}

async function saveMetadata(page, title = "") {
  await page.getByTestId("paper-metadata-edit").click();
  await page.getByLabel("标题").fill(title);
  await page.getByRole("button", { name: "保存" }).click();
  await page.getByTestId(title ? "paper-metadata-saved" : "paper-metadata-empty").waitFor();
}

async function clickBundle(page) {
  const requests = [];
  const handler = (request) => {
    if (new URL(request.url()).pathname.endsWith("/bundle")) requests.push(request);
  };
  page.on("request", handler);
  try {
    const downloadPromise = page.waitForEvent("download");
    const [download] = await Promise.all([downloadPromise, page.getByTestId("paper-metadata-bundle-export").click()]);
    const path = await download.path();
    assert(path, "bundle download did not expose a file");
    const bytes = await readFile(path);
    assert.equal(bytes.subarray(0, 2).toString("hex"), "504b", "bundle download was not a ZIP");
    assert.match(download.suggestedFilename(), /\.caeliae-paper\.zip$/);
    return { requests, filename: download.suggestedFilename() };
  } finally {
    page.off("request", handler);
  }
}

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const firstFilename = "bundle-ui-first.pdf";
  const firstUpload = await page.request.post(`${baseURL}/api/papers`, {
    multipart: { file: { name: firstFilename, mimeType: "application/pdf", buffer: Buffer.concat([pdf, Buffer.from("\n% bundle UI first\n")]) } },
  });
  assert.equal(firstUpload.status(), 201);

  await page.goto(`${baseURL}/reader/`);
  await paperCard(page, firstFilename).click();
  await page.getByText("1 / 2").waitFor();
  await openMetadata(page);
  await page.getByTestId("paper-metadata-empty").waitFor();

  const firstHref = `/api/papers/${(await firstUpload.json()).paper.paper_id}/bundle`;
  assert.equal(await page.getByTestId("paper-metadata-bundle-export").getAttribute("href"), firstHref);
  assert.equal(await page.getByTestId("paper-metadata-bundle-export").count(), 1);
  assert.equal(await page.getByTestId("paper-metadata-export").count(), 0);
  let download = await clickBundle(page);
  assert.equal(download.requests.length, 1, "bundle action issued more than one GET");

  await saveMetadata(page);
  assert.equal(await page.getByTestId("paper-metadata-bundle-export").count(), 1);
  assert.equal(await page.getByTestId("paper-metadata-export").count(), 0);

  await saveMetadata(page, "Bundle UI Title");
  assert.equal(await page.getByTestId("paper-metadata-export").count(), 1);
  assert.equal(await page.getByTestId("paper-metadata-bundle-export").count(), 1);
  assert.equal(await page.getByTestId("paper-metadata-export").getAttribute("href"), `/api/papers/${(await firstUpload.json()).paper.paper_id}/bibtex`);

  await page.getByTestId("paper-metadata-edit").click();
  assert.equal(await page.getByTestId("paper-metadata-bundle-export").count(), 0, "bundle export remained in edit mode");
  assert.equal(await page.getByTestId("paper-metadata-export").count(), 0, "BibTeX export remained in edit mode");
  await page.keyboard.press("Escape");
  await page.getByTestId("paper-metadata-view").waitFor();
  assert.equal(await page.getByTestId("paper-metadata-bundle-export").count(), 1, "Esc did not restore bundle export");

  await page.getByRole("button", { name: "返回" }).click();
  await page.locator(".library .side-panel-close").click();
  await page.getByRole("button", { name: "论文", exact: true }).click();
  await page.getByTestId("paper-metadata-open").click();
  assert.equal(await page.getByTestId("paper-metadata-bundle-export").getAttribute("href"), firstHref, "reopen used the wrong bundle href");
  await page.getByRole("button", { name: "返回" }).click();

  const secondFilename = "bundle-ui-second.pdf";
  const secondUpload = await page.request.post(`${baseURL}/api/papers`, {
    multipart: { file: { name: secondFilename, mimeType: "application/pdf", buffer: Buffer.concat([pdf, Buffer.from("\n% bundle UI second\n")]) } },
  });
  assert.equal(secondUpload.status(), 201);
  const secondPaperId = (await secondUpload.json()).paper.paper_id;
  const secondMetadata = await page.request.put(`${baseURL}/api/papers/${secondPaperId}/metadata`, {
    data: { title: "Second Bundle Paper", authors: null, publication_year: null, venue: null, doi: null, arxiv_id: null, citation_key: null },
  });
  assert.equal(secondMetadata.status(), 200);
  await page.reload();
  await paperCard(page, secondFilename).click();
  await page.getByText("1 / 2").waitFor();
  await openMetadata(page);
  assert.equal(await page.getByTestId("paper-metadata-bundle-export").getAttribute("href"), `/api/papers/${secondPaperId}/bundle`, "paper switch retained a stale bundle href");

  const actionLayout = await page.evaluate(() => {
    const read = (selector) => {
      const node = document.querySelector(selector);
      if (!node) return null;
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, whiteSpace: style.whiteSpace, flexShrink: style.flexShrink };
    };
    return {
      bundle: read('[data-testid="paper-metadata-bundle-export"]'),
      bibtex: read('[data-testid="paper-metadata-export"]'),
      edit: read('[data-testid="paper-metadata-edit"]'),
      overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    };
  });
  for (const [label, action] of Object.entries({ bundle: actionLayout.bundle, bibtex: actionLayout.bibtex, edit: actionLayout.edit })) {
    assert(action, `${label} action is missing`);
    assert(action.width >= 40, `${label} action became too narrow`);
    assert(action.height <= 44, `${label} action became too tall`);
    assert.equal(action.whiteSpace, "nowrap", `${label} action became multiline`);
    assert.equal(action.flexShrink, "0", `${label} action remained shrinkable`);
  }
  assert.equal(actionLayout.overflow, false);

  await page.setViewportSize({ width: 390, height: 844 });
  await wait(150);
  const narrow = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    bundleWhiteSpace: getComputedStyle(document.querySelector('[data-testid="paper-metadata-bundle-export"]')).whiteSpace,
  }));
  assert.equal(narrow.overflow, false);
  assert.equal(narrow.bundleWhiteSpace, "nowrap");

  console.log(JSON.stringify({
    bundle_visible_without_metadata: true,
    all_null_keeps_bundle_enabled: true,
    title_enables_bibtex: true,
    href_switch_safe: true,
    single_get_download: true,
    edit_escape_restores_actions: true,
    desktop_and_narrow_layout: true,
  }, null, 2));
} finally {
  await browser.close();
}
