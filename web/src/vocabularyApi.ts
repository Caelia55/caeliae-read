export type VocabularyEntryType = "word" | "phrase";
export interface VocabularyEntry { vocabulary_id: string; term: string; normalized_term: string; lemma: string | null; entry_type: VocabularyEntryType; definition: string | null; part_of_speech: string | null; pronunciation: string | null; examples: string[]; notes: string | null; tags: string[]; source_paper_id: string | null; source_paper_title?: string | null; source_session_id: string | null; source_page_number: number | null; source_selection: Record<string, unknown> | null; occurrence_count: number; created_at: string; updated_at: string; }
export interface VocabularyOccurrence { occurrence_id: string; vocabulary_id: string; surface_form: string; paper_id: string | null; paper_title: string | null; session_id: string | null; page_number: number | null; context_excerpt: string | null; selection: Record<string, unknown> | null; created_at: string; }
export interface VocabularyDetail { entry: VocabularyEntry; occurrences: VocabularyOccurrence[]; }
export interface VocabularyWritePayload { term: string; lemma: string | null; entry_type: VocabularyEntryType; definition: string | null; part_of_speech: string | null; pronunciation: string | null; examples: string[]; notes: string | null; tags: string[]; }
export type VocabularyLookupStatus = "ok" | "no_result" | "unsupported_phrase" | "unavailable";
export interface VocabularyLookupSource { kind: string; provider: string; version: string | null; }
export interface VocabularyLookupDefinition { text: string; source: VocabularyLookupSource; }
export interface VocabularyLookupCandidate { lemma: string; part_of_speech: string; definitions: VocabularyLookupDefinition[]; variants: string[]; }
export interface VocabularyLookupTranslation { text: string; source: VocabularyLookupSource; }
export interface VocabularyLookupOutput {
  query: string;
  normalized_query: string;
  entry_type: VocabularyEntryType;
  status: VocabularyLookupStatus;
  candidates: VocabularyLookupCandidate[];
  translations: VocabularyLookupTranslation[];
  message: string | null;
}
async function checked(response: Response): Promise<Response> { if (response.ok) return response; const body = await response.json().catch(() => ({})) as { detail?: { message?: string } }; throw new Error(body.detail?.message ?? `HTTP ${response.status}`); }
export async function listVocabulary(search = ""): Promise<VocabularyEntry[]> { const query = search ? `?search=${encodeURIComponent(search)}` : ""; return (await checked(await fetch(`/api/vocabulary${query}`)).then((response) => response.json()) as { entries: VocabularyEntry[] }).entries; }
export async function getVocabularyDetail(id: string): Promise<VocabularyDetail> { return checked(await fetch(`/api/vocabulary/${id}`)).then((response) => response.json()) as Promise<VocabularyDetail>; }
export async function createVocabulary(payload: VocabularyWritePayload & { idempotency_key: string }): Promise<VocabularyEntry> { const result = await checked(await fetch("/api/vocabulary", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })).then((response) => response.json()) as { entry: VocabularyEntry }; return result.entry; }
export async function updateVocabulary(id: string, payload: VocabularyWritePayload): Promise<VocabularyEntry> { const result = await checked(await fetch(`/api/vocabulary/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })).then((response) => response.json()) as { entry: VocabularyEntry }; return result.entry; }
export async function deleteVocabulary(id: string): Promise<void> { await checked(await fetch(`/api/vocabulary/${id}`, { method: "DELETE" })); }
export type ReviewRating = "unknown" | "fuzzy" | "known";
export async function recordVocabularyReview(payload: { vocabulary_id: string; review_session_id: string; rating: ReviewRating; mode?: "local" | "agent" }): Promise<void> { await checked(await fetch("/api/vocabulary/reviews", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: "local", ...payload }) })); }
export async function lookupVocabulary(text: string, locale = "en", targetLocale: string | null = null, signal?: AbortSignal): Promise<VocabularyLookupOutput> {
  return checked(await fetch("/api/vocabulary/lookup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, locale, target_locale: targetLocale }),
    signal,
  })).then((response) => response.json()) as Promise<VocabularyLookupOutput>;
}
export function vocabularyExportUrl(format: "json" | "csv"): string { return `/api/vocabulary/export?format=${format}`; }
