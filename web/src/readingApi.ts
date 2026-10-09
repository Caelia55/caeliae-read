import type { Paper } from "./api";

export type ReadingTaskStatus = "draft" | "active" | "archived";
export type ReadingSubmissionStatus = "draft" | "submitted" | "reviewed";
export type ReadingQuestionType = "short_text" | "single_choice";

export interface ReadingChoice { id: string; value: string; }
export interface ReadingQuestion {
  question_id: string;
  task_id: string;
  ordinal: number;
  question_type: ReadingQuestionType;
  prompt: string;
  choices: ReadingChoice[] | null;
  reference_answer: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReadingAnswer {
  question_id: string;
  answer_text: string | null;
  selected_choice: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface ReadingSubmission {
  submission_id: string;
  task_id: string;
  status: ReadingSubmissionStatus;
  revision: number;
  submitted_at: string | null;
  created_at: string;
  updated_at: string;
  answers: ReadingAnswer[];
}

export interface ReadingFeedback {
  feedback_id: string;
  submission_id: string;
  revision: number;
  feedback_text: string;
  question_feedback: Record<string, string> | null;
  source_kind: "manual" | "agent";
  created_at: string;
}

export interface ReadingTask {
  task_id: string;
  paper_id: string;
  title: string;
  instructions: string;
  status: ReadingTaskStatus;
  origin_kind: "manual" | "agent" | "daily";
  origin_ref: string | null;
  due_at: string | null;
  created_at: string;
  updated_at: string;
  paper: Paper | null;
  submission_status: ReadingSubmissionStatus | null;
  questions_count?: number;
}

export interface ReadingTaskDetail {
  task: ReadingTask;
  paper: Paper;
  questions: ReadingQuestion[];
  submission: ReadingSubmission | null;
  feedback: ReadingFeedback[];
  session_id?: string;
  mode?: "reading";
  read_only?: boolean;
}

interface ApiProblem { detail?: { message?: string; current_revision?: number } }

export class ReadingApiError extends Error {
  constructor(message: string, public readonly status: number, public readonly currentRevision?: number) {
    super(message);
  }
}

async function checked(response: Response): Promise<Response> {
  if (response.ok) return response;
  let problem: ApiProblem | undefined;
  try { problem = await response.json() as ApiProblem; } catch { /* no body */ }
  throw new ReadingApiError(problem?.detail?.message ?? `HTTP ${response.status}`, response.status, problem?.detail?.current_revision);
}

export async function listReadingTasks(status?: ReadingTaskStatus): Promise<ReadingTask[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return (await checked(await fetch(`/api/reading/tasks${query}`)).then((response) => response.json()) as { tasks: ReadingTask[] }).tasks;
}

export async function createReadingTask(payload: {
  paper_id: string;
  title: string;
  instructions: string;
  status: ReadingTaskStatus;
  origin_kind: "manual";
  questions: Array<{ ordinal: number; question_type: ReadingQuestionType; prompt: string; choices?: ReadingChoice[] | null; reference_answer?: string | null }>;
  idempotency_key: string;
}): Promise<ReadingTaskDetail> {
  return checked(await fetch("/api/reading/tasks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })).then((response) => response.json()) as Promise<ReadingTaskDetail>;
}

export async function getReadingTask(taskId: string): Promise<ReadingTaskDetail> {
  return checked(await fetch(`/api/reading/tasks/${encodeURIComponent(taskId)}`)).then((response) => response.json());
}

export async function openReadingTask(taskId: string): Promise<ReadingTaskDetail> {
  return checked(await fetch(`/api/reading/tasks/${encodeURIComponent(taskId)}/open`, { method: "POST" })).then((response) => response.json());
}

export async function getReadingSubmission(taskId: string): Promise<{ submission: ReadingSubmission | null }> {
  return checked(await fetch(`/api/reading/tasks/${encodeURIComponent(taskId)}/submission`)).then((response) => response.json());
}

export async function saveReadingDraft(taskId: string, payload: { revision: number; answers: ReadingAnswer[]; client_event_id: string }): Promise<{ submission: ReadingSubmission; replayed: boolean }> {
  return checked(await fetch(`/api/reading/tasks/${encodeURIComponent(taskId)}/submission`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })).then((response) => response.json());
}

export async function submitReading(taskId: string, payload: { revision: number; client_event_id: string }): Promise<{ submission: ReadingSubmission; replayed: boolean }> {
  return checked(await fetch(`/api/reading/tasks/${encodeURIComponent(taskId)}/submission/submit`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })).then((response) => response.json());
}

export async function listReadingFeedback(taskId: string): Promise<ReadingFeedback[]> {
  return (await checked(await fetch(`/api/reading/tasks/${encodeURIComponent(taskId)}/feedback`)).then((response) => response.json()) as { feedback: ReadingFeedback[] }).feedback;
}

export async function archiveReadingTask(taskId: string): Promise<{ task: ReadingTask }> {
  return checked(await fetch(`/api/reading/tasks/${encodeURIComponent(taskId)}/archive`, { method: "POST" })).then((response) => response.json());
}
