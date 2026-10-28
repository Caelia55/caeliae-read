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

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const paperCard = (page, filename) => page.getByRole("button", { name: new RegExp(`^${filename.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s`) });

async function openMetadata(page) {
  const trigger = page.getByTestId("paper-metadata-open");
  if (await trigger.isVisible()) {
    await trigger.click();
    return;
  }
  const papersButton = page.locator("nav.compact-toolbar").getByRole("button", { name: /^论文/ });
  await papersButton.click();
  await page.locator(".paper-metadata-drawer-trigger").click();
}

async function saveMetadata(page, values, expectedStatus = "saved") {
  await page.getByTestId("paper-metadata-edit").click();
  await page.getByLabel("标题").fill(values.title ?? "");
  await page.getByLabel("作者").fill(values.authors ?? "");
  await page.getByLabel("年份").fill(values.year ?? "");
  await page.getByLabel("期刊 / 会议").fill(values.venue ?? "");
  await page.getByLabel("DOI").fill(values.doi ?? "");
  await page.getByLabel("arXiv ID").fill(values.arxiv_id ?? "");
  await page.getByLabel("引用键").fill(values.citation_key ?? "");
  assert.equal(await page.getByTestId("paper-metadata-export").count(), 0, "export remained visible in edit mode");
  await page.getByRole("button", { name: "保存" }).click();
  await page.getByTestId(expectedStatus === "empty" ? "paper-metadata-empty" : "paper-metadata-saved").waitFor();
}

async function downloadFromUI(page) {
  const downloadPromise = page.waitForEvent("download");
  await page.getByTestId("paper-metadata-export").click();
  return downloadPromise;
}

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const externalRequests = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (["http:", "https:"].includes(url.protocol) && !["127.0.0.1", "localhost"].includes(url.hostname)) externalRequests.push(request.url());
  });

  let bibtexRequests = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.endsWith("/bibtex")) bibtexRequests += 1;
  });

  await page.goto(`${baseURL}/reader/`);
  await page.locator('input[type="file"]').setInputFiles(pdfPath);
  await page.getByText("1 / 2").waitFor();
  await openMetadata(page);
  await page.getByTestId("paper-metadata-empty").waitFor();
  assert.equal(await page.getByTestId("paper-metadata-export").count(), 0, "export was enabled without metadata");
  assert.equal(await page.getByTestId("paper-metadata-export-unavailable").innerText(), "请先填写文献信息");
  assert.equal(bibtexRequests, 0, "unavailable export triggered a BibTeX request");

  await saveMetadata(page, {}, "empty");
  assert.equal(await page.getByTestId("paper-metadata-saved").count(), 0, "all-null metadata row was presented as saved");
  const allNullMetadata = await (await page.request.get(`${baseURL}/api/papers/${(await (await page.request.get(`${baseURL}/api/papers`)).json()).papers[0].paper_id}/metadata`)).json();
  assert(allNullMetadata.metadata !== null, "all-null save removed the metadata row");
  await page.getByRole("button", { name: "返回" }).click();
  await openMetadata(page);
  await page.getByTestId("paper-metadata-empty").waitFor();

  await saveMetadata(page, { authors: "Ada Lovelace" });
  assert.equal(await page.getByTestId("paper-metadata-saved").count(), 1, "authors-only metadata was not presented as saved");
  assert.equal(await page.getByTestId("paper-metadata-export-unavailable").innerText(), "请先填写书目标题");
  await saveMetadata(page, { year: "2026" });
  assert.equal(await page.getByTestId("paper-metadata-saved").count(), 1, "year-only metadata was not presented as saved");
  assert.equal(await page.getByTestId("paper-metadata-export-unavailable").innerText(), "请先填写书目标题");

  await saveMetadata(page, { title: "A UI BibTeX Title" });
  assert.equal(await page.getByTestId("paper-metadata-fallback-hint").innerText(), "未填写 Citation key，将使用临时稳定引用键");
  const papersAfterFirstUpload = (await (await page.request.get(`${baseURL}/api/papers`)).json()).papers;
  const firstPaper = papersAfterFirstUpload.find((item) => item.original_filename === "selectable-paper.pdf");
  assert(firstPaper, "first acceptance paper was not found");
  const metadataBeforeFallbackExport = await (await page.request.get(`${baseURL}/api/papers/${firstPaper.paper_id}/metadata`)).json();
  const fallbackDownload = await downloadFromUI(page);
  assert.match(fallbackDownload.suggestedFilename(), /^caeliae-caeliae-[a-z0-9-]+\.bib$/);
  const fallbackBytes = await readFile(await fallbackDownload.path());
  const fallbackDirect = await page.request.get(`${baseURL}/api/papers/${firstPaper.paper_id}/bibtex`);
  assert.equal(fallbackDirect.status(), 200);
  assert.equal(Buffer.compare(fallbackBytes, await fallbackDirect.body()), 0, "download differs from direct BibTeX response");
  assert.equal(fallbackBytes.toString("utf8").includes("A UI BibTeX Title"), true);
  assert.equal(fallbackBytes.toString("utf8").includes("<html"), false);
  assert.equal(fallbackBytes.toString("utf8").includes('"detail"'), false);
  const metadataAfterFallbackExport = await (await page.request.get(`${baseURL}/api/papers/${firstPaper.paper_id}/metadata`)).json();
  assert.deepEqual(metadataAfterFallbackExport, metadataBeforeFallbackExport, "BibTeX export mutated metadata");
  assert.equal(metadataAfterFallbackExport.metadata.citation_key, null);

  await saveMetadata(page, { title: "A UI BibTeX Title", citation_key: "Smith:2026" });
  const explicitDownload = await downloadFromUI(page);
  assert.equal(explicitDownload.suggestedFilename(), "caeliae-Smith-2026.bib");
  const explicitText = (await readFile(await explicitDownload.path())).toString("utf8");
  assert(explicitText.startsWith("@misc{Smith:2026,"), "explicit citation key was not used");

  await page.getByTestId("paper-metadata-edit").click();
  assert.equal(await page.getByTestId("paper-metadata-export").count(), 0, "export remained visible in edit mode");
  await page.getByLabel("标题").fill("unsaved title");
  await page.keyboard.press("Escape");
  await page.getByTestId("paper-metadata-view").waitFor();
  assert.equal(await page.getByTestId("paper-metadata-export").count(), 1, "cancel did not restore export action");

  await page.getByRole("button", { name: "返回" }).click();
  await page.getByTestId("paper-metadata-open").click();
  await page.getByTestId("paper-metadata-saved").waitFor();
  assert.equal(await page.getByTestId("paper-metadata-export").count(), 1, "reopen lost export action");

  await page.getByRole("button", { name: "返回" }).click();
  const secondUpload = {
    name: "bibtex-second-paper.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.concat([await readFile(pdfPath), Buffer.from("\n% second synthetic upload\n")]),
  };
  await page.locator('input[type="file"]').setInputFiles(secondUpload);
  await page.getByText("1 / 2").waitFor();
  const papers = (await (await page.request.get(`${baseURL}/api/papers`)).json()).papers;
  const secondPaper = papers.find((item) => item.original_filename === secondUpload.name);
  assert(secondPaper, "second acceptance paper was not found");
  const secondMetadata = {
    title: "Second Paper",
    authors: ["Second Author"],
    publication_year: null,
    venue: null,
    doi: null,
    arxiv_id: null,
    citation_key: "Second:2026",
  };
  const put = await page.request.put(`${baseURL}/api/papers/${secondPaper.paper_id}/metadata`, { data: secondMetadata });
  assert.equal(put.status(), 200);
  await paperCard(page, secondUpload.name).click();
  await page.getByText("1 / 2").waitFor();
  await page.getByTestId("paper-metadata-open").click();
  await page.getByTestId("paper-metadata-saved").waitFor();
  assert.equal(await page.getByTestId("paper-metadata-export").getAttribute("href"), `/api/papers/${secondPaper.paper_id}/bibtex`, "export href used the wrong paper");

  await page.setViewportSize({ width: 390, height: 844 });
  await wait(150);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), false, "BibTeX action introduced responsive overflow");
  assert.equal(externalRequests.length, 0, `BibTeX flow made external requests: ${externalRequests.join(", ")}`);

  console.log(JSON.stringify({
    metadata_absent_blocked: true,
    title_required_blocked: true,
    fallback_download: true,
    explicit_key_download: true,
    downloaded_body_verified: true,
    no_write: true,
    edit_cancel: true,
    paper_switch_href: true,
    responsive: true,
    bibtex_requests: bibtexRequests,
  }, null, 2));
} finally {
  await browser.close();
}
