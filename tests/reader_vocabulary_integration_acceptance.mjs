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
  await page.locator(".text-layer span").filter({ hasText: "Selected passage" }).first().waitFor();
  const targetIndices = await page.locator(".text-layer span").evaluateAll((spans) => ({
    start: spans.findIndex((span) => span.textContent?.includes("Selected")),
    end: spans.findIndex((span) => span.textContent?.includes("verification")),
  }));
  if (targetIndices.start < 0 || targetIndices.end < 0) throw new Error("selectable target spans were not rendered");
  await page.locator(".text-layer span").nth(targetIndices.start).waitFor();

  async function selectTarget(fullText) {
    await page.locator(".text-layer").evaluate((layer, indices) => {
      const spans = Array.from(layer.querySelectorAll("span"));
      const startSpan = spans[indices.start];
      const endSpan = spans[indices.fullText === "word" ? indices.start : indices.end];
      if (!startSpan?.firstChild || !endSpan?.firstChild) throw new Error("selectable target spans were not rendered");
      const text = startSpan.textContent ?? "";
      const range = document.createRange();
      range.setStart(startSpan.firstChild, 0);
      const endOffset = indices.fullText === "word" ? Math.max(1, text.indexOf(" ")) : endSpan.textContent?.length ?? text.length;
      range.setEnd(endSpan.firstChild, endOffset);
      const selection = document.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
      layer.parentElement?.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
    }, { ...targetIndices, fullText });
    await page.getByTestId("selection-add-vocabulary").waitFor();
    return await page.evaluate(() => document.getSelection()?.toString().trim() ?? "");
  }

  async function addSelected(value) {
    const expectedTerm = await selectTarget(value);
    await page.getByTestId("selection-add-vocabulary").click();
    await page.getByText("已加入词汇").waitFor();
    const sessionId = await page.evaluate(() => localStorage.getItem("caeliae.read.activeSessionId"));
    let context;
    for (let attempt = 0; attempt < 40; attempt += 1) {
      const candidate = await (await page.request.get(`${baseURL}/api/sessions/${sessionId}/context`)).json();
      if (candidate.selection?.exact_text === expectedTerm) { context = candidate; break; }
      await page.waitForTimeout(250);
    }
    if (!context?.selection) throw new Error("selection was not persisted before vocabulary creation check");
    const term = context.selection.exact_text;
    const entries = await (await page.request.get(`${baseURL}/api/vocabulary`)).json();
    if (!entries.entries.some((entry) => entry.term === term)) throw new Error(`vocabulary entry missing: ${term}`);
    return { term, context };
  }

  const word = await addSelected("word");
  await page.getByRole("button", { name: "仅保存选区" }).click();
  await page.evaluate(() => document.getSelection()?.removeAllRanges());
  const phrase = await addSelected("phrase");
  await page.getByTestId("selection-add-vocabulary").click();
  await page.getByText("该选区已在词汇中").waitFor();

  await page.getByRole("button", { name: "词汇", exact: true }).click();
  await page.waitForURL("**/reader/vocabulary.html");
  const entry = page.locator(".entry-main").filter({ hasText: phrase.term }).first();
  await entry.click();
  const details = page.locator(".entry-details").filter({ hasText: phrase.term }).first();
  await details.waitFor();
  const detailText = await details.innerText();
  if (!detailText.includes("selectable-paper.pdf") || !detailText.includes("第 2 页") || !detailText.includes(phrase.term)) {
    throw new Error(`source details missing: ${detailText}`);
  }
  console.log(JSON.stringify({
    word: word.term,
    phrase: phrase.term,
    source_title: "selectable-paper.pdf",
    source_page: phrase.context.selection.page_number,
    duplicate_replayed: true,
    source_details_visible: true,
  }, null, 2));
} finally {
  await browser.close();
}
