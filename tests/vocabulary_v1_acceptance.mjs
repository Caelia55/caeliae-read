import { chromium } from "../web/node_modules/playwright-core/index.mjs";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const baseURL = requireAcceptanceBaseURL();
const pdfPath = process.env.CAELIAE_READ_ACCEPTANCE_PDF;
if (!pdfPath) throw new Error("CAELIAE_READ_ACCEPTANCE_PDF is required");
const browserPath = process.env.CAELIAE_READ_BROWSER_PATH
  ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";

const browser = await chromium.launch({ headless: true, executablePath: browserPath });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(`${baseURL}/reader/`);
  await page.locator('input[type="file"]').setInputFiles(pdfPath);
  await page.getByText("1 / 2").waitFor();
  await page.getByRole("button", { name: "下一页" }).click();
  await page.getByText("2 / 2").waitFor();

  const sessionId = await page.evaluate(() => localStorage.getItem("caeliae.read.activeSessionId"));
  if (!sessionId) throw new Error("reader session was not created");
  const papers = await (await page.request.get(`${baseURL}/api/papers`)).json();
  const paper = papers.papers.at(-1);
  if (!paper) throw new Error("acceptance paper was not uploaded");
  const selection = {
    paper_id: paper.paper_id,
    session_id: sessionId,
    page_number: 2,
    exact_text: "traces",
    prefix: "interaction ",
    suffix: " in the model.",
    normalized_quads: [{ x: 0.1, y: 0.12, width: 0.12, height: 0.03 }],
    page_width: 612,
    page_height: 792,
    rotation: 0,
    revision: 1,
  };
  const createdResponse = await page.request.post(`${baseURL}/api/vocabulary/from-selection`, {
    data: {
      term: "traces",
      examples: [],
      tags: ["reading"],
      selection,
      idempotency_key: crypto.randomUUID(),
    },
  });
  if (!createdResponse.ok()) throw new Error(`source vocabulary creation failed: ${await createdResponse.text()}`);
  const created = await createdResponse.json();
  const vocabularyId = created.entry.vocabulary_id;

  await page.goto(`${baseURL}/reader/vocabulary.html`);
  const row = page.locator(".entry-row").filter({ hasText: "traces" }).first();
  await row.waitFor();
  await row.locator(".entry-actions-trigger").click();
  await page.getByRole("button", { name: "编辑" }).click();
  await page.getByLabel("Lemma / canonical form").fill("trace");
  await page.getByLabel("词性").fill("noun");
  await page.getByLabel("当前语境含义").fill("context meaning");
  await page.locator(".advanced-fields summary").click();
  await page.getByLabel("标签").fill("reading, research");
  await page.getByRole("button", { name: "保存修改" }).click();
  await page.locator(".entry-term").filter({ hasText: "trace" }).first().waitFor();

  const secondSelection = { ...selection, exact_text: "tracing", page_number: 3 };
  const attachedResponse = await page.request.post(`${baseURL}/api/vocabulary/${vocabularyId}/occurrences`, {
    data: { selection: secondSelection, idempotency_key: crypto.randomUUID() },
  });
  if (!attachedResponse.ok()) throw new Error(`second occurrence attach failed: ${await attachedResponse.text()}`);

  await page.reload();
  const canonicalRow = page.locator(".entry-row").filter({ hasText: "trace" }).first();
  await canonicalRow.waitFor();
  if (!(await canonicalRow.locator(".entry-original").filter({ hasText: "traces" }).count())) throw new Error("original surface form is not shown beside canonical title");
  await canonicalRow.click();
  const details = canonicalRow.locator(".entry-details");
  await details.waitFor();
  await details.locator(".occurrence-item").nth(1).waitFor();
  const detailsText = await details.innerText();
  if (!detailsText.includes("traces") || !detailsText.includes("tracing") || !detailsText.includes("2 条来源记录")) throw new Error(`occurrence details incomplete: ${detailsText}`);

  const search = page.getByPlaceholder("搜索词条、lemma、释义或标签");
  async function assertOneSearch(query) {
    await search.fill(query);
    await page.waitForFunction(() => document.querySelectorAll(".entry-row").length === 1);
    const ids = await page.locator(".entry-row").evaluateAll((items) => items.map((item) => item.querySelector(".entry-term")?.textContent));
    if (ids.length !== 1 || ids[0]?.trim() !== "trace") throw new Error(`search did not resolve to one canonical entry: ${query}`);
  }
  for (const query of ["trace", "tracing", "interaction traces", "research"]) await assertOneSearch(query);
  await search.fill("");
  await page.waitForFunction(() => document.querySelectorAll(".entry-row").length === 1);

  await page.getByRole("button", { name: "开始复习" }).click();
  await page.getByRole("dialog", { name: "开始复习" }).getByRole("button", { name: "开始" }).click();
  await page.getByRole("heading", { name: "trace" }).waitFor();
  await page.getByRole("button", { name: "退出复习" }).click();

  const defaultJson = await page.request.get(`${baseURL}/api/vocabulary/export?format=json&search=trace`);
  const defaultJsonBody = await defaultJson.json();
  if (defaultJsonBody.entries.length !== 1 || defaultJsonBody.entries[0].occurrence_count !== 2) throw new Error("default JSON export is not entry-oriented");
  const defaultCsv = await page.request.get(`${baseURL}/api/vocabulary/export?format=csv&search=trace`);
  if (!defaultCsv.ok() || (await defaultCsv.text()).trim().split("\n").length !== 2) throw new Error("default CSV export is not one row per entry");
  const detailed = await page.request.get(`${baseURL}/api/vocabulary/export?format=json&detail=occurrences&search=trace`);
  const detailedBody = await detailed.json();
  if (detailedBody.entries.length !== 1 || detailedBody.entries[0].occurrences.length !== 2) throw new Error("detailed JSON export is missing occurrences");
  const occurrenceCsv = await page.request.get(`${baseURL}/api/vocabulary/export?format=csv-occurrences&search=trace`);
  const occurrenceCsvText = await occurrenceCsv.text();
  if (!occurrenceCsv.ok() || occurrenceCsvText.trim().split("\n").length !== 3 || !occurrenceCsvText.includes("traces") || !occurrenceCsvText.includes("tracing")) throw new Error("occurrence CSV export is not two rows");

  console.log(JSON.stringify({
    create_new_surface: "traces",
    canonical: "trace",
    attached_surface: "tracing",
    occurrence_count: 2,
    search_queries: ["trace", "tracing", "interaction traces", "research"],
    study_title: "trace",
    default_entry_export: true,
    detailed_occurrence_export: true,
    occurrence_csv_rows: 2,
  }, null, 2));
} finally {
  await browser.close();
}
