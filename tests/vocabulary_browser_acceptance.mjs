import { chromium } from "../web/node_modules/playwright-core/index.mjs";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const baseURL = requireAcceptanceBaseURL();
const browserPath = process.env.CAELIAE_READ_BROWSER_PATH
  ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const term = `phase2-browser-${Date.now()}`;

const browser = await chromium.launch({ headless: true, executablePath: browserPath });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(`${baseURL}/reader/vocabulary.html`);
  await page.getByRole("button", { name: /添加词汇/ }).click();
  await page.getByLabel("词条").fill(term);
  await page.getByRole("button", { name: "保存词汇" }).click();
  await page.locator(".entry-row").filter({ hasText: term }).waitFor();

  const search = page.getByPlaceholder("搜索词条、释义或标签");
  await search.fill(term);
  await page.locator(".entry-row").filter({ hasText: term }).waitFor();
  const jsonExport = await page.request.get(`${baseURL}/api/vocabulary/export?format=json&search=${encodeURIComponent(term)}`);
  const csvExport = await page.request.get(`${baseURL}/api/vocabulary/export?format=csv&search=${encodeURIComponent(term)}`);
  if (!jsonExport.ok() || !(await jsonExport.text()).includes(term)) throw new Error("JSON export missing browser-created entry");
  if (!csvExport.ok() || !(await csvExport.text()).includes(term)) throw new Error("CSV export missing browser-created entry");

  await page.getByRole("button", { name: "开始复习" }).click();
  await page.getByRole("dialog", { name: "开始复习" }).getByRole("button", { name: "开始" }).click();
  await page.getByRole("button", { name: "显示答案" }).click();
  await page.getByRole("button", { name: "会", exact: true }).click();
  await page.getByRole("button", { name: /查看结果|下一个/ }).click();
  await page.getByRole("button", { name: /回到词汇/ }).first().click();

  await page.getByRole("button", { name: new RegExp(`打开 ${term} 操作`) }).click();
  page.once("dialog", (dialog) => void dialog.accept());
  await page.getByRole("button", { name: "删除" }).click();
  await page.locator(".entry-row").filter({ hasText: term }).waitFor({ state: "detached" });

  console.log(JSON.stringify({ crud: true, search: true, json_export: true, csv_export: true, study_review: true }, null, 2));
} finally {
  await browser.close();
}
