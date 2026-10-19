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

const metadataPayload = {
  title: "A Normalized Paper Title",
  authors: ["Ada Lovelace", "Grace Hopper", "Ada Lovelace"],
  publication_year: 2026,
  venue: "Example Venue",
  doi: "10.1234/example.doi",
  arxiv_id: "2401.12345",
  citation_key: "Lovelace2026Example",
  updated_at: "2026-01-01T00:00:00Z",
};

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const paperCard = (page, filename) => page.getByRole("button", { name: new RegExp(`^${escapeRegExp(filename)}\\s`) });

async function openPaperMetadata(page) {
  const desktopTrigger = page.getByTestId("paper-metadata-open");
  if (await desktopTrigger.isVisible()) {
    await desktopTrigger.click();
    return;
  }
  const desktopPapersButton = page.locator("nav.desktop-toolbar").getByRole("button", { name: "论文", exact: true });
  if (await desktopPapersButton.isVisible()) {
    await desktopPapersButton.click();
    await desktopTrigger.click();
    return;
  }
  const compactPapersButton = page.locator("nav.compact-toolbar").getByRole("button", { name: /^论文/ });
  await compactPapersButton.click();
  await page.locator(".paper-metadata-drawer-trigger").click();
}

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const visiblePaperList = page.locator('[data-testid="paper-list-view"]:visible');
  let raceMode = false;
  let firstPaper;
  let racePaper;
  let getDelayUsed = false;
  let delayedGetStarted = () => {};
  let delayedPutStarted = () => {};
  const delayedGet = new Promise((resolve) => { delayedGetStarted = resolve; });
  const delayedPut = new Promise((resolve) => { delayedPutStarted = resolve; });
  await page.route("**/metadata", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const paperId = url.pathname.split("/").at(-2);
    try {
      if (raceMode && racePaper && request.method() === "GET" && paperId === racePaper.paper_id && !getDelayUsed) {
        getDelayUsed = true;
        delayedGetStarted();
        await wait(450);
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ paper_id: racePaper.paper_id, metadata: { ...metadataPayload, title: "STALE B GET" } }) });
        return;
      }
      if (raceMode && racePaper && request.method() === "PUT" && paperId === racePaper.paper_id) {
        delayedPutStarted();
        await wait(450);
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ paper_id: racePaper.paper_id, metadata: { ...metadataPayload, title: "STALE B PUT" } }) });
        return;
      }
      await route.continue();
    } catch {
      // The app may abort a stale request during a paper switch.
    }
  });
  await page.goto(`${baseURL}/reader/`);
  await page.locator('input[type="file"]').setInputFiles(pdfPath);
  await page.getByText("1 / 2").waitFor();

  const firstFilename = "selectable-paper.pdf";
  assert.equal(await visiblePaperList.count(), 1, "Papers view did not render its visible list");
  assert.equal(await page.getByTestId("paper-metadata-panel").count(), 0, "metadata rendered while Papers view was active");
  await openPaperMetadata(page);
  await page.getByTestId("paper-metadata-empty").waitFor();
  assert.equal(await page.getByTestId("paper-list-view").count(), 0, "paper list remained in metadata view");
  assert.equal(await page.getByRole("button", { name: "返回" }).count(), 1, "metadata view did not expose a keyboard-accessible back button");
  assert((await page.getByTestId("paper-metadata-panel").innerText()).includes(firstFilename), "empty metadata view did not show the original filename");

  await page.getByTestId("paper-metadata-edit").click();
  await page.getByLabel("标题").fill(metadataPayload.title);
  await page.getByLabel("作者").fill("Ada Lovelace\nGrace Hopper\nAda Lovelace");
  await page.getByLabel("年份").fill("2026");
  await page.getByLabel("期刊 / 会议").fill("  Example Venue  ");
  await page.getByLabel("DOI").fill("https://doi.org/10.1234/example.doi");
  await page.getByLabel("arXiv ID").fill("arXiv:2401.12345v2");
  await page.getByLabel("引用键").fill("Lovelace2026Example");
  await page.getByRole("button", { name: "保存" }).click();
  await page.getByTestId("paper-metadata-saved").waitFor();
  const savedText = await page.getByTestId("paper-metadata-view").innerText();
  assert(savedText.includes("A Normalized Paper Title"), "saved title is not displayed");
  assert(savedText.includes("Ada Lovelace、Grace Hopper、Ada Lovelace"), "author order or duplicate was not preserved");
  assert(savedText.includes("Example Venue"), "saved venue is not displayed");
  assert(savedText.includes("10.1234/example.doi"), "DOI was not normalized/displayed");
  assert(savedText.includes("2401.12345"), "arXiv ID was not normalized/displayed");
  const doi = page.getByTestId("paper-metadata-view").getByRole("link", { name: "10.1234/example.doi" });
  assert.equal(await doi.getAttribute("href"), "https://doi.org/10.1234%2Fexample.doi");
  assert.equal(await doi.getAttribute("target"), "_blank");
  assert.equal(await doi.getAttribute("rel"), "noopener noreferrer");
  const arxiv = page.getByTestId("paper-metadata-view").getByRole("link", { name: "2401.12345" });
  assert.equal(await arxiv.getAttribute("href"), "https://arxiv.org/abs/2401.12345");
  assert.equal(await arxiv.getAttribute("target"), "_blank");
  assert.equal(await arxiv.getAttribute("rel"), "noopener noreferrer");

  await page.getByTestId("paper-metadata-edit").click();
  await page.getByLabel("标题").fill("draft must be discarded");
  await page.keyboard.press("Escape");
  await page.getByTestId("paper-metadata-view").waitFor();
  assert(!(await page.getByTestId("paper-metadata-view").innerText()).includes("draft must be discarded"), "Escape did not discard metadata draft");

  async function assertValidation(fieldLabel, value) {
    if (!(await page.getByTestId("paper-metadata-form").isVisible())) await page.getByTestId("paper-metadata-edit").click();
    await page.getByLabel(fieldLabel).fill(value);
    await page.getByRole("button", { name: "保存" }).click();
    await page.getByTestId("paper-metadata-save-error").waitFor();
    assert.equal(await page.getByTestId("paper-metadata-form").count(), 1, `${fieldLabel} failure left edit mode`);
  }

  await assertValidation("年份", "999");
  await page.getByLabel("年份").fill("2026");
  await assertValidation("DOI", "not-a-doi");
  await page.getByLabel("DOI").fill(metadataPayload.doi);
  await assertValidation("arXiv ID", "not-an-arxiv-id");
  await page.getByLabel("arXiv ID").fill(metadataPayload.arxiv_id);
  await assertValidation("引用键", "bad key with spaces");
  await page.getByLabel("引用键").fill(metadataPayload.citation_key);
  await page.getByRole("button", { name: "保存" }).click();
  await page.getByTestId("paper-metadata-saved").waitFor();

  await page.getByTestId("paper-metadata-edit").click();
  for (const label of ["标题", "作者", "年份", "期刊 / 会议", "DOI", "arXiv ID", "引用键"]) await page.getByLabel(label).fill("");
  await page.getByRole("button", { name: "保存" }).click();
  await page.getByTestId("paper-metadata-saved").waitFor();
  assert(!(await page.getByTestId("paper-metadata-view").innerText()).includes("A Normalized Paper Title"), "cleared title remained visible");
  assert((await page.getByTestId("paper-metadata-view").innerText()).includes("—"), "cleared metadata did not display missing fields consistently");
  await page.getByRole("button", { name: "返回" }).click();
  await visiblePaperList.waitFor();
  assert.equal(await page.getByTestId("paper-metadata-panel").count(), 0, "back did not return to Papers view");
  assert.equal(await paperCard(page, firstFilename).locator("xpath=.").getAttribute("class"), "paper-card active", "back did not preserve selected paper");
  await page.getByTestId("paper-metadata-open").click();
  await page.getByTestId("paper-metadata-saved").waitFor();
  await page.getByRole("button", { name: "关闭论文面板" }).click();

  const secondUpload = {
    name: "second-selectable-paper.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.concat([await (await import("node:fs/promises")).readFile(pdfPath), Buffer.from("\n% second synthetic upload\n")]),
  };
  await page.locator('input[type="file"]').setInputFiles(secondUpload);
  await page.getByText("1 / 2").waitFor();
  const papersResponse = await page.request.get(`${baseURL}/api/papers`);
  assert(papersResponse.ok());
  const papers = (await papersResponse.json()).papers;
  assert.equal(papers.length, 2, "isolated acceptance did not create two distinct papers");
  firstPaper = papers.find((item) => item.original_filename === firstFilename);
  const secondPaper = papers.find((item) => item.original_filename === secondUpload.name);
  assert(firstPaper && secondPaper, "paper filenames for race acceptance were not found");
  racePaper = secondPaper;
  raceMode = true;
  await openPaperMetadata(page);
  await Promise.race([delayedGet, wait(5_000).then(() => { throw new Error("A metadata GET was not observed"); })]);
  await page.getByRole("button", { name: "返回" }).click();
  await visiblePaperList.waitFor();
  assert.equal(await page.getByTestId("paper-metadata-panel").count(), 0, "return during loading did not leave metadata view");
  await paperCard(page, firstFilename).click();
  await page.waitForFunction((paperId) => localStorage.getItem("caeliae.read.activePaperId") === paperId, firstPaper.paper_id);
  await page.getByTestId("paper-metadata-open").click();
  await page.getByTestId("paper-metadata-view").waitFor();
  await wait(600);
  assert(!(await page.getByTestId("paper-metadata-panel").innerText()).includes("STALE B GET"), "stale B GET overwrote A metadata view");

  await page.getByRole("button", { name: "返回" }).click();
  await wait(600);
  await paperCard(page, secondUpload.name).click();
  await page.waitForFunction((paperId) => localStorage.getItem("caeliae.read.activePaperId") === paperId, secondPaper.paper_id);
  await page.getByTestId("paper-metadata-open").click();
  await page.getByTestId("paper-metadata-panel").waitFor();
  await page.getByTestId("paper-metadata-empty").waitFor();
  await page.getByTestId("paper-metadata-edit").click();
  await page.getByLabel("标题").fill("pending A save");
  await page.getByRole("button", { name: "保存" }).click();
  await Promise.race([delayedPut, wait(5_000).then(() => { throw new Error("A metadata PUT was not observed"); })]);
  await page.getByRole("button", { name: "返回" }).click();
  await paperCard(page, firstFilename).click();
  await page.waitForFunction((paperId) => localStorage.getItem("caeliae.read.activePaperId") === paperId, firstPaper.paper_id);
  await page.getByTestId("paper-metadata-open").click();
  await page.getByTestId("paper-metadata-view").waitFor();
  await wait(600);
  assert(!(await page.getByTestId("paper-metadata-panel").innerText()).includes("STALE B PUT"), "stale B PUT overwrote A metadata view");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(150);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), false, "metadata UI introduced responsive overflow");
  await page.locator("nav.compact-toolbar").getByRole("button", { name: /^论文/ }).click();
  await page.locator(".paper-drawer").waitFor();
  await visiblePaperList.waitFor();
  await page.locator(".paper-metadata-drawer-trigger").click();
  const compactMetadataPanel = page.locator(".paper-drawer .paper-metadata-panel");
  await compactMetadataPanel.waitFor();
  assert.equal(await page.getByTestId("paper-list-view").count(), 0, "compact metadata view still rendered the paper list");
  await page.locator(".paper-metadata-edit:visible").click();
  await page.locator("#metadata-title:visible").fill("A very long title ".repeat(40));
  await page.locator("#metadata-doi:visible").fill(`10.1234/${"x".repeat(180)}`);
  const overflow = await page.evaluate(() => {
    const nodes = [document.documentElement, document.querySelector(".paper-drawer-panel"), document.querySelector(".paper-metadata-panel")].filter(Boolean);
    return nodes.map((node) => node.scrollWidth > node.clientWidth);
  });
  assert.deepEqual(overflow, [false, false, false], "metadata drawer introduced horizontal overflow with long fields");
  await page.keyboard.press("Escape");

  console.log(JSON.stringify({
    emptyView: true,
    fullSaveAndNormalization: true,
    multilineAuthors: true,
    externalLinks: true,
    cancelAndClear: true,
    invalidDraftPreserved: true,
    staleGetRejected: true,
    stalePutRejected: true,
    mutuallyExclusiveViews: true,
    backNavigation: true,
    narrowNoHorizontalOverflow: true,
    responsive: true,
  }, null, 2));
} finally {
  await browser.close();
}
