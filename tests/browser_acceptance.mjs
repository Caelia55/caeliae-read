import { chromium } from "../web/node_modules/playwright-core/index.mjs";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const baseURL = requireAcceptanceBaseURL();
const pdfPath = process.env.CAELIAE_READ_ACCEPTANCE_PDF;
if (!pdfPath) throw new Error("CAELIAE_READ_ACCEPTANCE_PDF is required");
const browserPath = process.env.CAELIAE_READ_BROWSER_PATH
  ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";

const browser = await chromium.launch({
  headless: true,
  executablePath: browserPath,
});
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(`${baseURL}/reader/`);
  await page.locator('input[type="file"]').setInputFiles(pdfPath);
  await page.getByText("1 / 2").waitFor();
  await page.getByRole("button", { name: "下一页" }).click();
  await page.getByText("2 / 2").waitFor();
  await page.locator(".text-layer span").filter({ hasText: "Selected passage" }).waitFor();

  await page.locator(".text-layer").evaluate((layer) => {
    const spans = Array.from(layer.querySelectorAll("span"));
    const target = spans.find((span) => span.textContent?.includes("Selected passage"));
    if (!target?.firstChild) throw new Error("selectable target span was not rendered");
    const range = document.createRange();
    range.selectNodeContents(target);
    const selection = document.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    layer.parentElement?.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
  });
  const activeSession = await page.evaluate(() => localStorage.getItem("caeliae.read.activeSessionId"));
  let state;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const response = await page.request.get(`${baseURL}/api/sessions/${activeSession}/context`);
    if (response.ok()) {
      const candidate = await response.json();
      if (candidate.page_number === 2 && candidate.selection?.exact_text?.includes("Selected passage")) {
        state = candidate;
        break;
      }
    }
    await page.waitForTimeout(250);
  }
  if (!state) throw new Error("page 2 selection was not persisted within 10 seconds");
  if (state.page_number !== 2) throw new Error(`expected page 2, got ${state.page_number}`);
  if (!state.selection?.exact_text?.includes("Selected passage")) {
    throw new Error("selection was not persisted through HTTP");
  }
  if (state.page_text.length > 4_000) throw new Error("page text exceeded its bound");

  await page.reload();
  await page.getByText("2 / 2").waitFor();
  for (const viewport of [
    { width: 1180, height: 800 },
    { width: 1024, height: 768 },
    { width: 768, height: 1024 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    await page.getByRole("button", { name: "上一页" }).waitFor();
    const bodyOverflows = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    if (bodyOverflows) throw new Error(`layout overflow at ${viewport.width}x${viewport.height}`);
  }
  console.log(JSON.stringify({
    paper_id: state.paper.paper_id,
    session_id: state.session_id,
    page_number: state.page_number,
    selection: state.selection.exact_text,
    restored_after_refresh: true,
    responsive_viewports_checked: 4,
  }));
} finally {
  await browser.close();
}
