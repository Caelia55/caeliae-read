import assert from "node:assert/strict";
import { chromium } from "../web/node_modules/playwright-core/index.mjs";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const baseURL = requireAcceptanceBaseURL();
const pdfPath = process.env.CAELIAE_READ_ACCEPTANCE_PDF;
if (!pdfPath) throw new Error("CAELIAE_READ_ACCEPTANCE_PDF is required");
const browserPath = process.env.CAELIAE_READ_BROWSER_PATH
  ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";

const candidateResult = (query) => ({
  query,
  normalized_query: query.toLowerCase(),
  entry_type: "word",
  status: "ok",
  candidates: [
    {
      lemma: "trace",
      part_of_speech: "noun",
      definitions: [{ text: "a mark left by something", source: { kind: "dictionary", provider: "wordnet", version: "3.0" } }],
      variants: [query],
    },
    {
      lemma: "trace",
      part_of_speech: "verb",
      definitions: [{ text: "to follow or discover by signs", source: { kind: "dictionary", provider: "wordnet", version: "3.0" } }],
      variants: [query],
    },
  ],
  translations: [],
  message: null,
});

const resultFor = (mode, query) => {
  if (mode === "no_result") return { ...candidateResult(query), status: "no_result", candidates: [] };
  if (mode === "unsupported_phrase") return { ...candidateResult(query), entry_type: "phrase", status: "unsupported_phrase", candidates: [] };
  if (mode === "unavailable") return { ...candidateResult(query), status: "unavailable", candidates: [], message: "fixture unavailable" };
  return candidateResult(query);
};

const browser = await chromium.launch({ headless: true, executablePath: browserPath });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  let mode = "ok";
  let delayedRoute = null;
  const requests = [];
  await page.route("**/api/vocabulary/lookup", async (route) => {
    const payload = JSON.parse(route.request().postData() ?? "{}");
    requests.push(payload);
    if (mode === "delay") {
      delayedRoute = route;
      return;
    }
    if (mode === "network_error") {
      await route.abort("failed");
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(resultFor(mode, payload.text)),
    });
  });

  await page.goto(`${baseURL}/reader/`);
  await page.locator('input[type="file"]').setInputFiles(pdfPath);
  await page.getByText("1 / 2").waitFor();
  await page.getByRole("button", { name: "下一页" }).click();
  await page.getByText("2 / 2").waitFor();

  async function openSelectionComposer() {
    await page.locator(".text-layer span").filter({ hasText: "Selected passage" }).first().waitFor();
    await page.locator(".text-layer").evaluate((layer) => {
      const span = Array.from(layer.querySelectorAll("span")).find((item) => item.textContent?.includes("Selected passage"));
      if (!span?.firstChild) throw new Error("controlled lookup selection span was not rendered");
      const range = document.createRange();
      range.selectNodeContents(span);
      const selection = document.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
      layer.parentElement?.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
    });
    await page.locator(".selection-composer").waitFor();
  }

  await openSelectionComposer();
  const lookupButton = page.getByTestId("selection-lookup");
  await lookupButton.focus();
  await page.keyboard.press("Enter");
  await page.getByTestId("selection-lookup-candidates").waitFor();
  assert.equal(requests.at(-1).locale, "en");
  assert.equal(requests.at(-1).target_locale, null);
  assert.equal(await page.getByTestId("selection-lookup-candidate").count(), 2, "all lookup candidates should be shown");
  assert.equal(await page.locator('[aria-selected="true"]').count(), 0, "lookup must not auto-select a candidate");
  assert.equal(await page.locator(".selection-lookup-candidate.active").count(), 0, "lookup must not visually select a candidate");
  assert.equal(await page.getByTestId("selection-lookup-source").first().innerText(), "WordNet 3.0");
  assert.equal(await page.locator(".selection-lookup-translations").count(), 0, "empty translations must not render a section");
  assert.equal(await page.getByTestId("selection-add-vocabulary").count(), 1);
  assert.equal(await page.getByTestId("selection-attach-vocabulary").count(), 1);
  await page.keyboard.press("Escape");
  await page.locator(".selection-composer").waitFor({ state: "detached" });

  for (const nextMode of ["no_result", "unsupported_phrase", "unavailable"]) {
    mode = nextMode;
    await openSelectionComposer();
    await page.getByTestId("selection-lookup").click();
    await page.getByTestId(`selection-lookup-${nextMode === "unsupported_phrase" ? "unsupported" : nextMode.replace("_", "-")}`).waitFor();
    await page.keyboard.press("Escape");
    await page.locator(".selection-composer").waitFor({ state: "detached" });
  }

  mode = "network_error";
  await openSelectionComposer();
  await page.getByTestId("selection-lookup").click();
  await page.getByTestId("selection-lookup-error").waitFor();
  await page.keyboard.press("Escape");
  await page.locator(".selection-composer").waitFor({ state: "detached" });

  mode = "delay";
  await openSelectionComposer();
  await page.getByTestId("selection-lookup").click();
  await page.getByTestId("selection-lookup-loading").waitFor();
  assert(delayedRoute, "delayed lookup request was not captured");
  await page.keyboard.press("Escape");
  await page.locator(".selection-composer").waitFor({ state: "detached" });

  mode = "ok";
  await openSelectionComposer();
  await page.getByTestId("selection-lookup").click();
  await page.getByTestId("selection-lookup-candidates").waitFor();
  try {
    await delayedRoute.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ...candidateResult("old selection"), candidates: [{ ...candidateResult("old selection").candidates[0], lemma: "stale-result" }] }),
    });
  } catch {
    // The browser may have canceled the old request together with the AbortController.
  }
  await page.waitForTimeout(100);
  assert.equal(await page.locator(".selection-lookup-candidate").count(), 2, "late response changed the current selection result");
  assert.equal(await page.getByText("stale-result", { exact: true }).count(), 0, "stale lookup candidate leaked into the current selection");

  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), false, "lookup composer overflowed narrow layout");
  await page.getByTestId("selection-lookup").focus();
  await page.keyboard.press("Enter");
  await page.getByTestId("selection-lookup-candidates").waitFor();

  console.log(JSON.stringify({
    lookup_requests: requests.length,
    multiple_candidates: true,
    no_auto_selection: true,
    source_label: "WordNet 3.0",
    empty_translation_hidden: true,
    statuses_checked: ["no_result", "unsupported_phrase", "unavailable", "network_error"],
    late_response_ignored: true,
    narrow_layout_overflow: false,
  }, null, 2));
} finally {
  await browser.close();
}
