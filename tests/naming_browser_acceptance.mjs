import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { chromium } from "../web/node_modules/playwright-core/index.mjs";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const baseURL = requireAcceptanceBaseURL();
if (!process.env.CAELIAE_READ_TEST_REAL_PDF) throw new Error("isolated URL and real PDF are required");
const browser = await chromium.launch({ headless: true, executablePath: process.env.CAELIAE_READ_BROWSER_PATH ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const upload = await page.request.post(`${baseURL}/api/papers`, { multipart: { file: { name: "migration-real-paper.pdf", mimeType: "application/pdf", buffer: await readFile(process.env.CAELIAE_READ_TEST_REAL_PDF) } } });
  assert(upload.ok());
  await page.goto(`${baseURL}/reader/`);
  await page.locator(".paper-card", { hasText: "migration-real-paper.pdf" }).click();
  await page.locator(".text-layer span").first().waitFor();
  await page.locator(".sync-saved").waitFor();
  const anchor = await page.evaluate(() => {
    const layer = document.querySelector(".text-layer");
    const span = Array.from(layer.querySelectorAll("span")).find(n => n.textContent.trim().length > 20);
    const rect = span.getBoundingClientRect(), pdf = document.querySelector(".pdf-page").getBoundingClientRect();
    return { paper_id: localStorage.getItem("caeliae.read.activePaperId"), session_id: localStorage.getItem("caeliae.read.activeSessionId"), page_number: 1,
      exact_text: span.textContent, prefix: "", suffix: "", normalized_quads: [{ x: (rect.left-pdf.left)/pdf.width, y: (rect.top-pdf.top)/pdf.height, width: rect.width/pdf.width, height: rect.height/pdf.height }],
      page_width: pdf.width, page_height: pdf.height, rotation: 0 };
  });
  const created = await page.request.post(`${baseURL}/api/annotations`, { data: { ...anchor, note: "isolated migration assistant remember", remember: true, idempotency_key: crypto.randomUUID() } });
  assert(created.ok());
  const annotation = (await created.json()).annotation;
  const user = await page.request.post(`${baseURL}/api/annotations/user`, { data: { ...anchor, note: "isolated same-anchor user note", remember: false, idempotency_key: crypto.randomUUID() } });
  assert(user.ok());
  const id = annotation.annotation_id;
  const mark = page.locator(`[data-annotation-id="${id}"]`).first();
  await mark.waitFor({ timeout: 7000 });
  await page.locator(".annotation-mark .annotation-entry-dot").last().click();
  await page.locator(".annotation-switcher button").filter({ hasText: "AI" }).click();
  await page.locator(".annotation-card.card-assistant").waitFor();
  assert.equal(await page.locator(".annotation-switcher button").count(), 2);
  await page.locator(".annotation-switcher button").filter({ hasText: "我" }).click();
  await page.locator(".annotation-card.card-user").waitFor();
  await page.locator(".annotation-switcher button").filter({ hasText: "AI" }).click();
  await page.getByRole("button", { name: "回到原文" }).click();
  assert(await mark.evaluate(n => n.classList.contains("is-focused")));
  await page.getByRole("button", { name: "关闭批注" }).click();
  await page.getByRole("button", { name: "下一页" }).click();
  await page.locator('.pdf-page canvas[aria-label="论文第 2 页"]').waitFor();
  await page.locator(".sync-saved").waitFor();
  const before = await page.evaluate(() => ({ paper: localStorage.getItem("caeliae.read.activePaperId"), session: localStorage.getItem("caeliae.read.activeSessionId") }));
  let pageTwoSaved = false;
  for (let attempt = 0; attempt < 40; attempt++) {
    const response = await page.request.get(`${baseURL}/api/sessions/${before.session}/context`);
    if (response.ok() && (await response.json()).checkpoint.page_number === 2) { pageTwoSaved = true; break; }
    await page.waitForTimeout(250);
  }
  assert(pageTwoSaved, "page-2 checkpoint not saved");
  await page.waitForTimeout(750);
  assert.equal((await (await page.request.get(`${baseURL}/api/sessions/${before.session}/context`)).json()).checkpoint.page_number, 2);
  await page.evaluate(({paper,session}) => {
    localStorage.setItem("coread.activePaperId", paper); localStorage.setItem("coread.activeSessionId", session);
    localStorage.removeItem("caeliae.read.activePaperId"); localStorage.removeItem("caeliae.read.activeSessionId");
  }, before);
  await page.reload();
  await page.waitForTimeout(1500);
  await page.locator('.pdf-page canvas[aria-label="论文第 2 页"]').waitFor();
  const after = await page.evaluate(() => ({ paper: localStorage.getItem("caeliae.read.activePaperId"), session: localStorage.getItem("caeliae.read.activeSessionId"), oldPaper: localStorage.getItem("coread.activePaperId"), oldSession: localStorage.getItem("coread.activeSessionId") }));
  assert.equal(after.paper, before.paper); assert.equal(after.session, before.session);
  assert.equal(after.oldPaper, before.paper); assert.equal(after.oldSession, before.session);
  await page.getByRole("button", { name: "上一页" }).click();
  await mark.waitFor(); await page.locator(".annotation-mark .annotation-entry-dot").last().click();
  await page.locator(".annotation-switcher button").filter({ hasText: "AI" }).click();
  await page.getByRole("button", { name: "回到原文" }).click();
  await page.locator(".annotation-card.card-assistant").waitFor();
  const userId = (await user.json()).annotation.annotation_id;
  const removed = await page.request.delete(`${baseURL}/api/annotations/${userId}?session_id=${anchor.session_id}`);
  assert.equal((await removed.json()).deleted, true);
  console.log(JSON.stringify({ legacyStorageRestore: { before, after, page: 2 }, sameAnchorSwitch: true, sourceFocusAfterPageNavigation: true, rememberAnnotationId: id }));
} finally { await browser.close(); }
