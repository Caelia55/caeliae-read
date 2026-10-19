import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { chromium } from "../web/node_modules/playwright-core/index.mjs";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const baseURL = requireAcceptanceBaseURL();
const browserPath = process.env.CAELIAE_READ_BROWSER_PATH
  ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const outputDir = process.env.CAELIAE_READ_LOOKUP_OUTPUT ?? ".artifacts/vocabulary-lookup-save";
await mkdir(outputDir, { recursive: true });

const lookupResponse = (query) => ({
  query,
  normalized_query: query.toLowerCase(),
  entry_type: "word",
  status: "ok",
  candidates: [
    {
      lemma: "trace",
      part_of_speech: "noun",
      definitions: [{ text: "a confirmed noun definition", source: { kind: "dictionary", provider: "wordnet", version: "3.0-fixture" } }],
      variants: [query],
    },
    {
      lemma: "trace",
      part_of_speech: "verb",
      definitions: [{ text: "a confirmed verb definition", source: { kind: "dictionary", provider: "wordnet", version: "3.0-fixture" } }],
      variants: [query],
    },
  ],
  translations: [],
  message: null,
});

const browser = await chromium.launch({ headless: true, executablePath: browserPath });
try {
  const maker = await browser.newPage();
  await maker.setContent(`<style>@page{size:8.5in 11in;margin:.7in}body{font:20px/1.8 Arial;margin:0}p{margin:0 0 18px}</style><p>traces</p><p>tracing</p>`);
  const fixturePath = `${outputDir}/lookup-save.pdf`;
  await maker.pdf({ path: fixturePath, width: "8.5in", height: "11in", printBackground: true });
  await maker.close();

  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  let failNextSave = true;
  const savePayloads = [];
  await page.route("**/api/vocabulary/lookup", async (route) => {
    const payload = route.request().postDataJSON();
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(lookupResponse(payload.text)) });
  });
  let delayNextSave = false;
  let delayedSaveRoute = null;
  await page.route("**/api/vocabulary/from-selection", async (route) => {
    savePayloads.push(route.request().postDataJSON());
    if (failNextSave) {
      failNextSave = false;
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: { message: "controlled save failure" } }) });
      return;
    }
    if (delayNextSave) {
      delayNextSave = false;
      delayedSaveRoute = route;
      return;
    }
    await route.continue();
  });

  await page.goto(`${baseURL}/reader/`);
  await page.locator('input[type="file"]').setInputFiles(fixturePath);
  await page.getByText("1 / 1").waitFor();

  async function selectWord(word) {
    if (await page.locator(".selection-composer").count()) {
      await page.keyboard.press("Escape");
      await page.locator(".selection-composer").waitFor({ state: "detached" });
    }
    const target = page.locator(".text-layer span").filter({ hasText: word }).first();
    await target.waitFor();
    const box = await target.boundingBox();
    if (!box || box.width < 4 || box.height < 4) throw new Error(`lookup-save selection target is not selectable: ${word}`);
    const y = box.y + box.height / 2;
    await page.mouse.move(box.x + 1, y);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width - 1, y, { steps: 6 });
    await page.mouse.up();
    await page.locator(".selection-composer").waitFor();
  }

  await selectWord("traces");
  await page.getByTestId("selection-lookup").click();
  await page.getByTestId("selection-lookup-candidates").waitFor();
  assert.equal(await page.getByTestId("selection-lookup-candidate").count(), 2);
  assert.equal(await page.locator('[aria-selected="true"]').count(), 0);

  const nounCandidate = page.getByTestId("selection-lookup-candidate").filter({ hasText: "noun" }).first();
  const useCandidate = nounCandidate.getByTestId("selection-lookup-use-candidate");
  await useCandidate.focus();
  await page.keyboard.press("Enter");
  await page.getByTestId("selection-lookup-edit").waitFor();
  assert.equal(await page.getByLabel("Lemma / canonical form").inputValue(), "trace");
  assert.equal(await page.getByLabel("词性").inputValue(), "noun");
  await page.getByLabel("当前语境含义").fill("confirmed by the reader");

  const saveButton = page.getByTestId("selection-lookup-save-new");
  await saveButton.click();
  await page.getByText("保存查词结果失败：controlled save failure", { exact: true }).waitFor();
  assert.equal(await page.getByTestId("selection-lookup-edit").count(), 1, "save failure discarded the edit state");
  assert.equal(await page.getByLabel("当前语境含义").inputValue(), "confirmed by the reader");

  await saveButton.click();
  await page.getByText("已加入词汇：trace", { exact: true }).waitFor();
  assert.equal(await saveButton.isDisabled(), true, "successful save remained clickable");
  const createdPayload = savePayloads.at(-1);
  assert.equal(createdPayload.term, "traces");
  assert.equal(createdPayload.lemma, "trace");
  assert.equal(createdPayload.part_of_speech, "noun");
  assert.equal(createdPayload.definition, "confirmed by the reader");
  assert.equal("notes" in createdPayload, false);
  assert.equal("tags" in createdPayload, false);

  const entriesAfterCreate = await (await page.request.get(`${baseURL}/api/vocabulary?search=traces`)).json();
  assert.equal(entriesAfterCreate.entries.length, 1);
  const createdEntry = entriesAfterCreate.entries[0];
  assert.equal(createdEntry.term, "traces");
  assert.equal(createdEntry.lemma, "trace");
  assert.equal(createdEntry.part_of_speech, "noun");
  assert.equal(createdEntry.definition, "confirmed by the reader");
  assert.equal(createdEntry.occurrence_count, 1);
  const replay = await page.request.post(`${baseURL}/api/vocabulary/from-selection`, { data: createdPayload });
  assert.equal(replay.status(), 201);
  assert.equal((await replay.json()).replayed, true);

  await selectWord("tracing");
  await page.getByTestId("selection-lookup").click();
  await page.getByTestId("selection-lookup-candidates").waitFor();
  const beforeAttach = await (await page.request.get(`${baseURL}/api/vocabulary/${createdEntry.vocabulary_id}`)).json();
  assert.equal(beforeAttach.entry.occurrence_count, 1, "lookup automatically attached the second surface form");
  await page.getByTestId("selection-attach-vocabulary").click();
  await page.getByRole("dialog", { name: "选择已有词条" }).waitFor();
  const attachOption = page.locator(".selection-attach-option").filter({ hasText: "trace" }).first();
  await attachOption.waitFor();
  await attachOption.click();
  await page.getByText("已添加到 trace", { exact: true }).waitFor();
  assert.equal(savePayloads.at(-1).vocabulary_id, createdEntry.vocabulary_id);
  assert.equal("lemma" in savePayloads.at(-1), false);
  const finalDetail = await (await page.request.get(`${baseURL}/api/vocabulary/${createdEntry.vocabulary_id}`)).json();
  assert.equal(finalDetail.entry.term, "traces");
  assert.equal(finalDetail.entry.lemma, "trace");
  assert.equal(finalDetail.entry.part_of_speech, "noun");
  assert.equal(finalDetail.entry.definition, "confirmed by the reader");
  assert.equal(finalDetail.entry.occurrence_count, 2);
  assert.deepEqual(finalDetail.occurrences.map((item) => item.surface_form), ["traces", "tracing"]);

  await selectWord("traces");
  await page.getByTestId("selection-lookup").click();
  await page.getByTestId("selection-lookup-candidates").waitFor();
  await page.getByTestId("selection-lookup-use-candidate").first().click();
  delayNextSave = true;
  await page.getByTestId("selection-lookup-save-new").click();
  for (let attempt = 0; attempt < 30 && !delayedSaveRoute; attempt += 1) await page.waitForTimeout(50);
  assert(delayedSaveRoute, "delayed lookup save request was not intercepted");
  await page.keyboard.press("Escape");
  await page.locator(".selection-composer").waitFor({ state: "detached" });
  const messageBeforeStaleResponse = await page.locator(".reader-status").innerText();
  await delayedSaveRoute.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ entry: createdEntry, created: false, replayed: true }) });
  await page.waitForTimeout(200);
  assert.equal(await page.locator(".reader-status").innerText(), messageBeforeStaleResponse, "stale lookup save response changed closed-composer UI");

  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), false, "lookup save state overflowed narrow layout");
  console.log(JSON.stringify({
    created_term: finalDetail.entry.term,
    canonical: finalDetail.entry.lemma,
    occurrence_surfaces: finalDetail.occurrences.map((item) => item.surface_form),
    save_failure_retry: true,
    idempotency_replay: true,
    explicit_attach_only: true,
    stale_save_ignored_after_close: true,
    narrow_layout_overflow: false,
  }, null, 2));
} finally {
  await browser.close();
}
