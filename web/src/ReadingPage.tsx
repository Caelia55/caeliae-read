import { FormEvent, useEffect, useState } from "react";
import { listPapers, Paper } from "./api";
import { createReadingTask, listReadingTasks, ReadingChoice, ReadingQuestionType, ReadingTask } from "./readingApi";

function statusLabel(status: ReadingTask["status"]): string {
  return status === "active" ? "进行中" : status === "draft" ? "草稿" : "已归档";
}

function submissionLabel(status: ReadingTask["submission_status"]): string {
  return status === "submitted" ? "已提交" : status === "reviewed" ? "已批改" : status === "draft" ? "草稿" : "未开始";
}

function parseChoices(value: string): ReadingChoice[] | null {
  const choices = value.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
    const [id, ...rest] = line.split("=");
    return { id: id.trim(), value: rest.join("=").trim() };
  });
  return choices.length ? choices : null;
}

function CreateTaskForm({ papers, onCreated, onCancel }: { papers: Paper[]; onCreated: () => void; onCancel: () => void }) {
  const [paperId, setPaperId] = useState(papers[0]?.paper_id ?? "");
  const [title, setTitle] = useState("");
  const [instructions, setInstructions] = useState("");
  const [prompt, setPrompt] = useState("");
  const [questionType, setQuestionType] = useState<ReadingQuestionType>("short_text");
  const [choices, setChoices] = useState("");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!paperId || !title.trim() || !prompt.trim()) return;
    setSaving(true);
    setMessage("创建中…");
    try {
      await createReadingTask({
        paper_id: paperId,
        title: title.trim(),
        instructions: instructions.trim(),
        status: "active",
        origin_kind: "manual",
        questions: [{ ordinal: 1, question_type: questionType, prompt: prompt.trim(), choices: questionType === "single_choice" ? parseChoices(choices) : null }],
        idempotency_key: crypto.randomUUID(),
      });
      onCreated();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "创建失败");
    } finally {
      setSaving(false);
    }
  }

  return <form className="reading-create-form" onSubmit={(event) => void submit(event)}>
    <div className="reading-form-heading"><div><span className="reading-eyebrow">MANUAL TASK</span><h2>新建阅读任务</h2></div><button type="button" className="reading-close" onClick={onCancel} aria-label="关闭新建任务">×</button></div>
    <label>使用论文<select value={paperId} onChange={(event) => setPaperId(event.target.value)} required>{papers.map((paper) => <option key={paper.paper_id} value={paper.paper_id}>{paper.original_filename}</option>)}</select></label>
    <label>任务标题<input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="例如：理解这篇论文的核心论点" required /></label>
    <label>说明<textarea value={instructions} onChange={(event) => setInstructions(event.target.value)} rows={2} placeholder="可选" /></label>
    <label>第一个问题<textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} rows={2} placeholder="你想带着什么问题阅读？" required /></label>
    <label>题型<select value={questionType} onChange={(event) => setQuestionType(event.target.value as ReadingQuestionType)}><option value="short_text">简答</option><option value="single_choice">单选</option></select></label>
    {questionType === "single_choice" && <label>选项<textarea value={choices} onChange={(event) => setChoices(event.target.value)} rows={3} placeholder={"a=选项一\nb=选项二"} required /></label>}
    <div className="reading-form-footer"><span role="status">{message}</span><div><button type="button" className="button button-quiet" onClick={onCancel}>取消</button><button type="submit" className="button button-primary" disabled={saving || !papers.length}>{saving ? "创建中…" : "创建任务"}</button></div></div>
  </form>;
}

export function ReadingPage() {
  const [tasks, setTasks] = useState<ReadingTask[]>([]);
  const [papers, setPapers] = useState<Paper[]>([]);
  const [message, setMessage] = useState("加载中…");
  const [showCreate, setShowCreate] = useState(false);

  async function refresh() {
    try {
      const [nextTasks, nextPapers] = await Promise.all([listReadingTasks(), listPapers()]);
      setTasks(nextTasks);
      setPapers(nextPapers);
      setMessage(nextTasks.length ? "已同步" : "还没有阅读任务");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "加载失败");
    }
  }

  useEffect(() => { void refresh(); }, []);

  return <main className="reading-page">
    <header className="reading-page-header"><div><span className="reading-eyebrow">CAELIAE READ</span><h1>Reading <span>/ 阅读任务</span></h1><p>{tasks.length} 个任务 · {message}</p></div><nav><a href="/reader/">返回 Reader</a><a href="/reader/vocabulary.html">词汇</a><button type="button" className="button button-primary" onClick={() => setShowCreate((value) => !value)}>新建任务</button></nav></header>
    {showCreate && <CreateTaskForm papers={papers} onCreated={() => { setShowCreate(false); void refresh(); }} onCancel={() => setShowCreate(false)} />}
    <section className="reading-task-list" aria-label="Reading 任务列表">
      {tasks.map((task) => <article className={`reading-task-row reading-task-${task.status}`} key={task.task_id}>
        <div className="reading-task-main"><div className="reading-task-title-line"><h2>{task.title}</h2><span className={`reading-status reading-status-${task.status}`}>{statusLabel(task.status)}</span></div><p>{task.paper?.original_filename ?? "论文不可用"}</p><div className="reading-task-meta"><span>{submissionLabel(task.submission_status)}</span>{task.due_at && <span>截止 {task.due_at}</span>}<span>{task.questions_count ? `${task.questions_count} 题` : "阅读任务"}</span></div></div><a className="reading-task-open" href={`/reader/?mode=reading&task_id=${encodeURIComponent(task.task_id)}`}>{task.status === "archived" ? "查看" : "打开任务"} <span aria-hidden="true">→</span></a>
      </article>)}
      {!tasks.length && <div className="reading-empty"><span>R</span><h2>还没有 Reading 任务</h2><p>从一篇已有论文开始，给这次阅读留下一个问题。</p><button type="button" className="button button-quiet" onClick={() => setShowCreate(true)}>新建第一个任务</button></div>}
    </section>
  </main>;
}
