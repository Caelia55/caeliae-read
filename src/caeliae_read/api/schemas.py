from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SummaryNoteCreateInput(StrictModel):
    paper_id: UUID
    page_number: Annotated[int, Field(ge=1)]
    normalized_y: Annotated[float, Field(ge=0, le=1)]
    normalized_x: float | None = Field(default=None, ge=0, le=1)
    text: Annotated[str, Field(min_length=1, max_length=2_000)]

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text must not be blank")
        return value


class SummaryNoteUpdateInput(StrictModel):
    text: Annotated[str, Field(min_length=1, max_length=2_000)]

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text must not be blank")
        return value


class NormalizedQuad(StrictModel):
    x: Annotated[float, Field(ge=0, le=1)]
    y: Annotated[float, Field(ge=0, le=1)]
    width: Annotated[float, Field(gt=0, le=1)]
    height: Annotated[float, Field(gt=0, le=1)]


class SelectionInput(StrictModel):
    paper_id: UUID
    session_id: UUID
    page_number: Annotated[int, Field(ge=1)]
    exact_text: Annotated[str, Field(min_length=1, max_length=2_000)]
    prefix: Annotated[str, Field(max_length=160)]
    suffix: Annotated[str, Field(max_length=160)]
    normalized_quads: Annotated[list[NormalizedQuad], Field(min_length=1, max_length=64)]
    page_width: Annotated[float, Field(gt=0, le=100_000)]
    page_height: Annotated[float, Field(gt=0, le=100_000)]
    rotation: Literal[0, 90, 180, 270]
    revision: Annotated[int, Field(ge=1)]


class ReadingStateInput(StrictModel):
    paper_id: UUID
    session_id: UUID
    client_event_id: UUID
    page_number: Annotated[int, Field(ge=1)]
    page_text: Annotated[str, Field(max_length=4_000)]
    revision: Annotated[int, Field(ge=1)]
    selection: SelectionInput | None


class ReadingStateSaved(StrictModel):
    saved: Literal[True]
    replayed: bool
    paper_id: UUID
    session_id: UUID
    page_number: int
    revision: int
    updated_at: str


class VocabularyInput(StrictModel):
    term: Annotated[str, Field(min_length=1, max_length=200)]
    lemma: Annotated[str | None, Field(max_length=200)] = None
    entry_type: Literal["word", "phrase"] | None = None
    definition: Annotated[str | None, Field(max_length=4_000)] = None
    part_of_speech: Annotated[str | None, Field(max_length=80)] = None
    pronunciation: Annotated[str | None, Field(max_length=160)] = None
    examples: Annotated[list[str], Field(max_length=20)] = []
    notes: Annotated[str | None, Field(max_length=4_000)] = None
    tags: Annotated[list[str], Field(max_length=20)] = []
    source_paper_id: UUID | None = None
    source_session_id: UUID | None = None
    source_page_number: Annotated[int | None, Field(ge=1)] = None
    source_selection: dict[str, object] | None = None
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)]


class VocabularyUpdateInput(StrictModel):
    term: Annotated[str, Field(min_length=1, max_length=200)]
    lemma: Annotated[str | None, Field(max_length=200)] = None
    entry_type: Literal["word", "phrase"] | None = None
    definition: Annotated[str | None, Field(max_length=4_000)] = None
    part_of_speech: Annotated[str | None, Field(max_length=80)] = None
    pronunciation: Annotated[str | None, Field(max_length=160)] = None
    examples: Annotated[list[str], Field(max_length=20)] = []
    notes: Annotated[str | None, Field(max_length=4_000)] = None
    tags: Annotated[list[str], Field(max_length=20)] = []


class VocabularyFromSelectionInput(StrictModel):
    term: Annotated[str, Field(min_length=1, max_length=200)]
    lemma: Annotated[str | None, Field(max_length=200)] = None
    entry_type: Literal["word", "phrase"] | None = None
    vocabulary_id: UUID | None = None
    selection: SelectionInput
    definition: Annotated[str | None, Field(max_length=4_000)] = None
    part_of_speech: Annotated[str | None, Field(max_length=80)] = None
    pronunciation: Annotated[str | None, Field(max_length=160)] = None
    examples: Annotated[list[str], Field(max_length=20)] = []
    notes: Annotated[str | None, Field(max_length=4_000)] = None
    tags: Annotated[list[str], Field(max_length=20)] = []
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)]


class VocabularyOccurrenceInput(StrictModel):
    """V1 occurrence append is intentionally selection-backed only."""

    selection: SelectionInput
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)]


class LookupSource(StrictModel):
    kind: Literal["dictionary", "translation", "ai"]
    provider: Annotated[str, Field(min_length=1, max_length=80)]
    version: Annotated[str | None, Field(max_length=80)] = None


class LookupDefinition(StrictModel):
    text: Annotated[str, Field(min_length=1, max_length=4_000)]
    source: LookupSource


class LookupCandidate(StrictModel):
    lemma: Annotated[str | None, Field(max_length=200)] = None
    part_of_speech: Annotated[str | None, Field(max_length=80)] = None
    definitions: Annotated[list[LookupDefinition], Field(max_length=20)] = Field(default_factory=list)
    variants: Annotated[list[str], Field(max_length=50)] = Field(default_factory=list)


class LookupTranslation(StrictModel):
    text: Annotated[str, Field(min_length=1, max_length=4_000)]
    language: Annotated[str, Field(min_length=2, max_length=32)]
    source: LookupSource


class VocabularyLookupInput(StrictModel):
    text: Annotated[str, Field(min_length=1, max_length=200)]
    locale: Annotated[str, Field(min_length=2, max_length=32)] = "en"
    target_locale: Annotated[str | None, Field(min_length=2, max_length=32)] = None

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text must not be blank")
        return value

    @field_validator("locale")
    @classmethod
    def normalize_locale(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("locale must not be blank")
        return value

    @field_validator("target_locale")
    @classmethod
    def normalize_target_locale(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        return value or None


class VocabularyLookupOutput(StrictModel):
    query: str
    normalized_query: str
    entry_type: Literal["word", "phrase"]
    status: Literal["ok", "no_result", "unsupported_phrase", "unavailable"]
    candidates: list[LookupCandidate] = Field(default_factory=list)
    translations: list[LookupTranslation] = Field(default_factory=list)
    message: str | None = None


class VocabularyEntryOutput(StrictModel):
    vocabulary_id: str
    term: str
    normalized_term: str
    definition: str | None = None
    part_of_speech: str | None = None
    pronunciation: str | None = None
    examples: list[str] = Field(default_factory=list)
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)
    source_paper_id: str | None = None
    source_session_id: str | None = None
    source_page_number: int | None = None
    source_selection: dict[str, object] | None = None
    source_paper_title: str | None = None
    created_at: str
    updated_at: str
    lemma: str | None = None
    entry_type: Literal["word", "phrase"] = "word"
    occurrence_count: int = 0


class VocabularyOccurrenceOutput(StrictModel):
    occurrence_id: str
    vocabulary_id: str
    surface_form: str
    paper_id: str | None = None
    paper_title: str | None = None
    session_id: str | None = None
    page_number: int | None = None
    context_excerpt: str | None = None
    selection: dict[str, object] | None = None
    created_at: str


class VocabularyListOutput(StrictModel):
    entries: list[VocabularyEntryOutput]


class VocabularyEntryResponse(StrictModel):
    entry: VocabularyEntryOutput


class VocabularyMutationOutput(StrictModel):
    entry: VocabularyEntryOutput
    created: bool = False
    replayed: bool = False
    occurrence: VocabularyOccurrenceOutput | None = None
    occurrence_created: bool = False
    attached: bool = False


class VocabularyDetailOutput(StrictModel):
    entry: VocabularyEntryOutput
    occurrences: list[VocabularyOccurrenceOutput]


class VocabularyReviewInput(StrictModel):
    vocabulary_id: UUID
    review_session_id: UUID
    rating: Literal["unknown", "fuzzy", "known"]
    mode: Literal["local", "agent"] = "local"


class ReadingChoice(StrictModel):
    id: Annotated[str, Field(min_length=1, max_length=80)]
    value: Annotated[str, Field(min_length=1, max_length=400)]


class ReadingQuestionInput(StrictModel):
    ordinal: Annotated[int, Field(ge=1)]
    question_type: Literal["short_text", "single_choice"]
    prompt: Annotated[str, Field(min_length=1, max_length=2_000)]
    choices: Annotated[list[ReadingChoice] | None, Field(max_length=32)] = None
    reference_answer: Annotated[str | None, Field(max_length=4_000)] = None

    @model_validator(mode="after")
    def validate_choices(self) -> "ReadingQuestionInput":
        if self.question_type == "short_text" and self.choices:
            raise ValueError("short_text questions must not include choices")
        if self.question_type == "single_choice":
            if not self.choices:
                raise ValueError("single_choice questions require choices")
            ids = [choice.id for choice in self.choices]
            if len(ids) != len(set(ids)):
                raise ValueError("choice ids must be unique")
        return self


class ReadingTaskCreateInput(StrictModel):
    paper_id: UUID
    title: Annotated[str, Field(min_length=1, max_length=200)]
    instructions: Annotated[str, Field(max_length=4_000)] = ""
    status: Literal["draft", "active", "archived"] = "active"
    origin_kind: Literal["manual", "agent", "daily"] = "manual"
    origin_ref: Annotated[str | None, Field(max_length=200)] = None
    due_at: Annotated[str | None, Field(max_length=64)] = None
    questions: Annotated[list[ReadingQuestionInput], Field(min_length=1, max_length=100)]
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)]


class ReadingAnswerInput(StrictModel):
    question_id: UUID
    answer_text: Annotated[str | None, Field(max_length=4_000)] = None
    selected_choice: Annotated[str | None, Field(max_length=80)] = None


class ReadingDraftInput(StrictModel):
    revision: Annotated[int, Field(ge=0)] = 0
    answers: Annotated[list[ReadingAnswerInput], Field(max_length=100)] = []
    client_event_id: UUID


class ReadingSubmitInput(StrictModel):
    revision: Annotated[int, Field(ge=1)]
    client_event_id: UUID


class ReadingFeedbackInput(StrictModel):
    source_kind: Literal["manual", "agent"]
    feedback_text: Annotated[str, Field(min_length=1, max_length=4_000)]
    question_feedback: dict[str, Annotated[str, Field(max_length=2_000)]] | None = None
    idempotency_key: Annotated[str | None, Field(min_length=8, max_length=128)] = None

