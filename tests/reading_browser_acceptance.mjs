import { chromium } from "../web/node_modules/playwright-core/index.mjs";
import { readFile } from "node:fs/promises";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const baseURL = requireAcceptanceBaseURL();
const pdfPath = process.env.CAELIAE_READ_ACCEPTANCE_PDF;
if (!pdfPath) throw new Error("CAELIAE_READ_ACCEPTANCE_PDF is required");
const browserPath = process.env.CAELIAE_READ_BROWSER_PATH ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const browser = await chromium.launch({ headless: true, executablePath: browserPath });

async function expectOk(response, label) {
  if (!response.ok()) throw new Error(`${label}: HTTP ${response.status()} ${await response.text()}`);
  return response;
}

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const pdf = await readFile(pdfPath);
  const upload = await expectOk(await page.request.post(`${baseURL}/api/papers`, { multipart: { file: { name: "reading-mvp.pdf", mimeType: "application/pdf", buffer: pdf } } }), "upload");
  const paper = (await upload.json()).paper;
  const papersSession = await expectOk(await page.request.post(`${baseURL}/api/papers/${paper.paper_id}/sessions`), "paper session");
  const paperSession = await papersSession.json();
  const paperState = {
    paper_id: paper.paper_id, session_id: paperSession.session_id, client_event_id: crypto.randomUUID(),
    page_number: 1, page_text: "Papers state before Reading", revision: 1, selection: null,
  };
  await expectOk(await page.request.put(`${baseURL}/api/sessions/${paperSession.session_id}/state`, { data: paperState }), "paper state");
  await page.goto(`${baseURL}/reader/`);
  await page.evaluate(({ paperId, sessionId }) => { localStorage.setItem("caeliae.read.activePaperId", paperId); localStorage.setItem("caeliae.read.activeSessionId", sessionId); }, { paperId: paper.paper_id, sessionId: paperSession.session_id });

  const taskResponse = await expectOk(await page.request.post(`${baseURL}/api/reading/tasks`, { data: {
    paper_id: paper.paper_id, title: `Browser Reading ${Date.now()}`, instructions: "Read and answer.", status: "active", origin_kind: "manual",
    questions: [
      { ordinal: 1, question_type: "short_text", prompt: "What did you notice?" },
      { ordinal: 2, question_type: "single_choice", prompt: "Choose one.", choices: [{ id: "a", value: "Option A" }, { id: "b", value: "Option B" }] },
    ], idempotency_key: crypto.randomUUID(),
  } }), "task create");
  const task = await taskResponse.json();
  const taskId = task.task.task_id;

  await page.goto(`${baseURL}/reader/reading.html`);
  await page.getByText(task.task.title).waitFor();
  await page.getByRole("article").filter({ hasText: task.task.title }).getByRole("link").click();
  await page.getByText("1 / 2").waitFor();
  await page.getByRole("complementary", { name: "Reading 任务问题" }).waitFor();
  const answer = page.getByPlaceholder("写下你的回答…");
  await answer.fill("A useful observation.");
  await page.getByLabel("Option B").check();
  await page.getByRole("button", { name: "保存草稿" }).click();
  await page.getByRole("complementary", { name: "Reading 任务问题" }).getByText("草稿已保存").waitFor();
  await page.reload();
  await page.getByText("1 / 2").waitFor();
  if (await page.getByPlaceholder("写下你的回答…").inputValue() !== "A useful observation.") throw new Error("draft answer was not restored");
  await page.getByRole("button", { name: "提交阅读" }).click();
  await page.getByRole("complementary", { name: "Reading 任务问题" }).getByText("已提交", { exact: true }).waitFor();
  if (!(await answer.isDisabled())) throw new Error("submitted answer remained editable");

  const taskDetail = await (await page.request.post(`${baseURL}/api/reading/tasks/${taskId}/open`)).json();
  let context;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const taskContext = await page.request.get(`${baseURL}/api/sessions/${taskDetail.session_id}/context`);
    if (taskContext.ok()) { context = await taskContext.json(); break; }
    await page.waitForTimeout(200);
  }
  if (!context) throw new Error("task reading state was not persisted");
  const annotation = await expectOk(await page.request.post(`${baseURL}/api/annotations/user`, { data: {
    paper_id: paper.paper_id, session_id: taskDetail.session_id, page_number: context.page_number, exact_text: "Reading shared annotation", prefix: "", suffix: "",
    normalized_quads: [{ x: .1, y: .1, width: .2, height: .03 }], page_width: 612, page_height: 792, rotation: 0, note: "shared", remember: false, style_key: "primary", mark_type: "underline", idempotency_key: crypto.randomUUID(),
  } }), "shared annotation");
  if (!(await annotation.json()).annotation) throw new Error("shared annotation response missing");
  await expectOk(await page.request.post(`${baseURL}/api/sticky-notes`, { data: { paper_id: paper.paper_id, page: context.page_number, x: .3, y: .3, text: "Reading sticky", style_key: "secondary" } }), "shared sticky");
  await expectOk(await page.request.post(`${baseURL}/api/summary-notes`, { data: { paper_id: paper.paper_id, page_number: context.page_number, normalized_y: .46, text: "Reading shared summary" } }), "shared summary");
  await expectOk(await page.request.post(`${baseURL}/api/reading/tasks/${taskId}/feedback`, { data: { source_kind: "manual", feedback_text: "Good first pass." } }), "feedback");
  await page.reload();
  await page.getByText("Good first pass.").waitFor();
  await page.locator('.summary-note-marker[aria-label="大意：Reading shared summary"]').waitFor();

  await page.goto(`${baseURL}/reader/`);
  await page.locator(".paper-card.active").waitFor();
  await page.locator(".annotation-mark").filter({ has: page.locator("span") }).count();
  await page.locator(".annotation-mark").first().waitFor();
  await page.locator(".sticky-note-marker").first().waitFor();
  await page.locator('.summary-note-marker[aria-label="大意：Reading shared summary"]').waitFor();
  console.log(JSON.stringify({ task_list: true, open_task: true, draft_restore: true, submit_read_only: true, feedback: true, shared_annotation: true, shared_sticky: true, shared_summary: true, papers_state_preserved: true }, null, 2));
} finally {
  await browser.close();
}
