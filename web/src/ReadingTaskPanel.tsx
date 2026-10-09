import { useEffect, useMemo, useState } from "react";
import { ReadingApiError, ReadingTaskDetail, ReadingAnswer, ReadingSubmission, saveReadingDraft, submitReading } from "./readingApi";

function formatDate(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { dateStyle: "short", timeStyle: "short" });
}

function answerMap(submission: ReadingSubmission | null): Record<string, ReadingAnswer> {
  return Object.fromEntries((submission?.answers ?? []).map((answer) => [answer.question_id, answer]));
}

export function ReadingTaskPanel({ detail, onClose, onMessage }: { detail: ReadingTaskDetail; onClose: () => void; onMessage: (message: string) => void }) {
  const [submission, setSubmission] = useState<ReadingSubmission | null>(detail.submission);
  const [answers, setAnswers] = useState<Record<string, ReadingAnswer>>(() => answerMap(detail.submission));
  const [busy, setBusy] = useState(false);
  const [localMessage, setLocalMessage] = useState("");
  const latestFeedback = detail.feedback.length ? detail.feedback[detail.feedback.length - 1] : null;
  const readOnly = detail.read_only === true || submission?.status === "submitted" || submission?.status === "reviewed";

  useEffect(() => {
    setSubmission(detail.submission);
    setAnswers(answerMap(detail.submission));
  }, [detail.submission]);

  const answerPayload = useMemo(() => detail.questions.map((question) => {
    const answer = answers[question.question_id];
    return {
      question_id: question.question_id,
      answer_text: question.question_type === "short_text" ? (answer?.answer_text ?? null) : null,
      selected_choice: question.question_type === "single_choice" ? (answer?.selected_choice ?? null) : null,
    };
  }), [answers, detail.questions]);

  function setAnswer(questionId: string, update: Partial<ReadingAnswer>) {
    setAnswers((current) => {
      const existing = current[questionId] ?? { question_id: questionId, answer_text: null, selected_choice: null };
      return { ...current, [questionId]: { ...existing, ...update } };
    });
  }

  async function saveDraft(): Promise<ReadingSubmission | null> {
    if (readOnly || busy) return submission;
    setBusy(true);
    setLocalMessage("保存中…");
    try {
      const result = await saveReadingDraft(detail.task.task_id, {
        revision: submission?.revision ?? 0,
        answers: answerPayload,
        client_event_id: crypto.randomUUID(),
      });
      setSubmission(result.submission);
      setAnswers(answerMap(result.submission));
      setLocalMessage(result.replayed ? "草稿已恢复" : "草稿已保存");
      onMessage(result.replayed ? "草稿请求已重放" : "草稿已保存");
      return result.submission;
    } catch (error) {
      const message = error instanceof ReadingApiError && error.status === 409
        ? `草稿版本冲突${typeof error.currentRevision === "number" ? `（服务器版本 ${error.currentRevision}）` : "，请刷新后重试"}`
        : error instanceof Error ? error.message : "草稿保存失败";
      setLocalMessage(message);
      onMessage(message);
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function handleSubmit() {
    if (readOnly || busy) return;
    const incomplete = detail.questions.some((question) => {
      const answer = answers[question.question_id];
      return question.question_type === "short_text" ? !(answer?.answer_text ?? "").trim() : !answer?.selected_choice;
    });
    if (incomplete) {
      const message = "请先回答全部问题";
      setLocalMessage(message);
      onMessage(message);
      return;
    }
    const draft = submission ?? await saveDraft();
    if (!draft) return;
    setBusy(true);
    setLocalMessage("提交中…");
    try {
      const result = await submitReading(detail.task.task_id, { revision: draft.revision, client_event_id: crypto.randomUUID() });
      setSubmission(result.submission);
      setAnswers(answerMap(result.submission));
      setLocalMessage("已提交");
      onMessage("Reading 已提交");
    } catch (error) {
      const message = error instanceof ReadingApiError && error.status === 409 ? "提交状态发生变化，请刷新后重试" : error instanceof Error ? error.message : "提交失败";
      setLocalMessage(message);
      onMessage(message);
    } finally {
      setBusy(false);
    }
  }

  return <aside className="reading-task-panel" aria-label="Reading 任务问题">
    <div className="reading-panel-heading">
      <div><span className="reading-panel-kicker">QUESTIONS</span><strong>任务问题</strong></div>
      <button type="button" className="side-panel-close" aria-label="关闭问题面板" onClick={onClose}>×</button>
    </div>
    <div className="reading-task-instructions">{detail.task.instructions || "完成下面的问题，再提交本次阅读。"}</div>
    <div className="reading-question-list">
      {detail.questions.map((question, index) => {
        const answer = answers[question.question_id];
        const questionFeedback = latestFeedback?.question_feedback?.[question.question_id];
        return <section className="reading-question" key={question.question_id}>
          <div className="reading-question-label"><span>{index + 1}</span><strong>{question.prompt}</strong></div>
          {question.question_type === "short_text" ? <textarea
            value={answer?.answer_text ?? ""}
            disabled={readOnly || busy}
            onChange={(event) => setAnswer(question.question_id, { answer_text: event.target.value, selected_choice: null })}
            placeholder="写下你的回答…"
            rows={4}
          /> : <div className="reading-choice-list">{(question.choices ?? []).map((choice) => <label key={choice.id} className="reading-choice"><input type="radio" name={`question-${question.question_id}`} value={choice.id} checked={answer?.selected_choice === choice.id} disabled={readOnly || busy} onChange={() => setAnswer(question.question_id, { selected_choice: choice.id, answer_text: null })} /><span>{choice.value}</span></label>)}</div>}
          {questionFeedback && <p className="reading-question-feedback">反馈：{questionFeedback}</p>}
        </section>;
      })}
    </div>
    {latestFeedback ? <section className="reading-feedback"><span className="reading-panel-kicker">LATEST FEEDBACK</span><p>{latestFeedback.feedback_text}</p><small>{formatDate(latestFeedback.created_at)}</small></section> : submission?.status === "submitted" ? <p className="reading-waiting">已提交，等待反馈。</p> : null}
    <div className="reading-submit-area">
      <span role="status">{localMessage || (submission?.status === "reviewed" ? "已批改" : submission?.status === "submitted" ? `已提交 ${formatDate(submission.submitted_at)}` : submission ? "草稿" : "尚未开始")}</span>
      {!readOnly && <div className="reading-actions"><button type="button" className="button button-quiet" disabled={busy} onClick={() => void saveDraft()}>保存草稿</button><button type="button" className="button button-primary" disabled={busy} onClick={() => void handleSubmit()}>提交阅读</button></div>}
      {readOnly && submission?.submitted_at && <small>提交于 {formatDate(submission.submitted_at)}</small>}
    </div>
  </aside>;
}
