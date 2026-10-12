import assert from "node:assert/strict";
import { chromium } from "../web/node_modules/playwright-core/index.mjs";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const baseURL = requireAcceptanceBaseURL();
const browserPath = process.env.CAELIAE_READ_BROWSER_PATH
  ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";

const browser = await chromium.launch({ headless: true, executablePath: browserPath });
try {
  const page = await browser.newPage({ viewport: { width: 1024, height: 768 } });
  await page.goto(`${baseURL}/reader/`);
  const result = await page.evaluate(async () => {
    const response = await fetch("/api/vocabulary/lookup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: "traces", locale: "en", target_locale: null }),
    });
    return { status: response.status, body: await response.json() };
  });
  assert.equal(result.status, 200);
  assert.equal(result.body.status, "ok");
  assert(result.body.candidates.length > 0, "real WordNet browser lookup returned no candidates");
  assert(result.body.candidates.some((candidate) => candidate.lemma === "trace"));
  assert(result.body.candidates.some((candidate) => candidate.definitions.some((definition) => definition.source.provider === "wordnet")));
  assert.equal(result.body.translations.length, 0);
  console.log(JSON.stringify({
    query: result.body.query,
    status: result.body.status,
    candidate_count: result.body.candidates.length,
    has_trace_candidate: true,
    provider: "wordnet",
  }, null, 2));
} finally {
  await browser.close();
}
