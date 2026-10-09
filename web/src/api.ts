export interface Paper {
  paper_id: string;
  original_filename: string;
  size_bytes: number;
  page_count: number;
  created_at: string;
}

export interface Quad {
  x: number;
  y: number;
  width: number;
  height: number;
}

export type UserAnnotationStyle = "primary" | "secondary" | "tertiary";
export type UserAnnotationMarkType = "underline" | "highlight";

export interface SelectionState {
  paper_id: string;
  session_id: string;
  page_number: number;
  exact_text: string;
  prefix: string;
  suffix: string;
  normalized_quads: Quad[];
  page_width: number;
  page_height: number;
  rotation: 0 | 90 | 180 | 270;
  revision: number;
}

export interface ReadingContext {
  paper: Paper;
  session_id: string;
  page_number: number;
  page_text: string;
  checkpoint: {
    paper_id: string;
    session_id: string;
    page_number: number;
    revision: number;
    updated_at: string;
  };
  selection: SelectionState | null;
}

export interface Annotation {
  annotation_id: string;
  thread_id: string;
  paper_id: string;
  session_id: string;
  page_number: number;
  exact_text: string;
  prefix: string;
  suffix: string;
  normalized_quads: Quad[];
  page_width: number;
  page_height: number;
  rotation: 0 | 90 | 180 | 270;
  author: "user" | "assistant";
  note: string | null;
  remember: boolean;
  style_key: UserAnnotationStyle | null;
  mark_type: UserAnnotationMarkType;
  created_at: string;
  updated_at: string;
}

export interface VocabularyEntry {
  vocabulary_id: string;
  term: string;
  normalized_term: string;
  definition: string | null;
  part_of_speech: string | null;
  pronunciation: string | null;
  examples: string[];
  notes: string | null;
  tags: string[];
  source_paper_id: string | null;
  source_paper_title?: string | null;
  source_session_id: string | null;
  source_page_number: number | null;
  source_selection: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface StickyNote {
  id: string;
  paper_id: string;
  page: number;
  x: number;
  y: number;
  text: string;
  style_key: UserAnnotationStyle;
  created_at: string;
  updated_at: string;
}

export interface SummaryNote {
  summary_note_id: string;
  paper_id: string;
  page_number: number;
  normalized_y: number;
  text: string;
  created_at: string;
  updated_at: string;
}

interface ApiProblem {
  detail?: { code?: string; message?: string; current_revision?: number };
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly problem?: ApiProblem,
  ) {
    super(message);
  }
}

async function checked(response: Response): Promise<Response> {
  if (response.ok) return response;
  let problem: ApiProblem | undefined;
  try { problem = await response.json() as ApiProblem; } catch { /* no body */ }
  throw new ApiError(problem?.detail?.message ?? `HTTP ${response.status}`, response.status, problem);
}

export async function uploadPaper(file: File): Promise<{ paper: Paper; deduplicated: boolean }> {
  const body = new FormData();
  body.append("file", file, file.name);
  return checked(await fetch("/api/papers", { method: "POST", body })).then((r) => r.json());
}

export async function listPapers(): Promise<Paper[]> {
  const result = await checked(await fetch("/api/papers")).then((r) => r.json()) as { papers: Paper[] };
  return result.papers;
}

export async function createSession(paperId: string): Promise<{ paper_id: string; session_id: string }> {
  return checked(await fetch(`/api/papers/${paperId}/sessions`, { method: "POST" })).then((r) => r.json());
}

export async function getContext(sessionId: string): Promise<ReadingContext> {
  return checked(await fetch(`/api/sessions/${sessionId}/context`)).then((r) => r.json());
}

export async function listAnnotations(paperId: string, pageNumber?: number): Promise<Annotation[]> {
  const query = new URLSearchParams({ paper_id: paperId });
  if (pageNumber !== undefined) query.set("page_number", String(pageNumber));
  const result = await checked(await fetch(`/api/annotations?${query}`)).then((r) => r.json()) as { annotations: Annotation[] };
  return result.annotations;
}

export async function createUserAnnotation(payload: Record<string, unknown>): Promise<{ annotation: Annotation }> {
  return checked(await fetch("/api/annotations/user", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })).then((r) => r.json());
}

export async function createVocabularyFromSelection(payload: { term: string; selection: SelectionState; idempotency_key: string }): Promise<{ entry: VocabularyEntry; created: boolean; replayed: boolean }> {
  return checked(await fetch("/api/vocabulary/from-selection", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })).then((r) => r.json());
}

export async function updateUserAnnotation(annotationId: string, payload: Record<string, unknown>): Promise<{ annotation: Annotation }> {
  return checked(await fetch(`/api/annotations/${annotationId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })).then((r) => r.json());
}

export async function deleteUserAnnotation(annotationId: string, sessionId: string): Promise<void> {
  await checked(await fetch(`/api/annotations/${annotationId}?session_id=${encodeURIComponent(sessionId)}`, { method: "DELETE" }));
}

export async function revokeAssistantRemember(annotationId: string): Promise<{ annotation: Annotation }> {
  return checked(await fetch(`/api/annotations/${annotationId}/remember/revoke`, { method: "POST" })).then((r) => r.json());
}

export async function listStickyNotes(paperId: string, page?: number): Promise<StickyNote[]> {
  const query = new URLSearchParams({ paper_id: paperId });
  if (page !== undefined) query.set("page", String(page));
  const result = await checked(await fetch(`/api/sticky-notes?${query}`)).then((r) => r.json()) as { sticky_notes: StickyNote[] };
  return result.sticky_notes;
}

export async function createStickyNote(payload: Record<string, unknown>): Promise<{ sticky_note: StickyNote }> {
  return checked(await fetch("/api/sticky-notes", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })).then((r) => r.json());
}

export async function updateStickyNote(noteId: string, payload: Record<string, unknown>): Promise<{ sticky_note: StickyNote }> {
  return checked(await fetch(`/api/sticky-notes/${noteId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })).then((r) => r.json());
}

export async function deleteStickyNote(noteId: string): Promise<void> {
  await checked(await fetch(`/api/sticky-notes/${noteId}`, { method: "DELETE" }));
}

export async function listSummaryNotes(paperId: string, pageNumber?: number): Promise<SummaryNote[]> {
  const query = new URLSearchParams({ paper_id: paperId });
  if (pageNumber !== undefined) query.set("page_number", String(pageNumber));
  const result = await checked(await fetch(`/api/summary-notes?${query}`)).then((r) => r.json()) as { summary_notes: SummaryNote[] };
  return result.summary_notes;
}

export async function createSummaryNote(payload: { paper_id: string; page_number: number; normalized_y: number; text: string }): Promise<{ summary_note: SummaryNote }> {
  return checked(await fetch("/api/summary-notes", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })).then((r) => r.json());
}

export async function updateSummaryNote(summaryNoteId: string, payload: { text: string }): Promise<{ summary_note: SummaryNote }> {
  return checked(await fetch(`/api/summary-notes/${summaryNoteId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })).then((r) => r.json());
}

export async function deleteSummaryNote(summaryNoteId: string): Promise<void> {
  await checked(await fetch(`/api/summary-notes/${summaryNoteId}`, { method: "DELETE" }));
}

export function paperFileUrl(paperId: string): string {
  return `/api/papers/${paperId}/file`;
}

export interface StateWrite {
  paper_id: string;
  session_id: string;
  client_event_id: string;
  page_number: number;
  page_text: string;
  revision: number;
  selection: SelectionState | null;
}

export async function saveState(sessionId: string, payload: StateWrite): Promise<{ revision: number; replayed: boolean }> {
  const body = JSON.stringify(payload);
  let lastError: unknown;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      return await checked(await fetch(`/api/sessions/${sessionId}/state`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body,
      })).then((r) => r.json());
    } catch (error) {
      lastError = error;
      if (error instanceof ApiError) throw error;
    }
  }
  throw lastError;
}
