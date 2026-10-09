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
    definition: Annotated[str | None, Field(max_length=4_000)] = None
    part_of_speech: Annotated[str | None, Field(max_length=80)] = None
    pronunciation: Annotated[str | None, Field(max_length=160)] = None
    examples: Annotated[list[str], Field(max_length=20)] = []
    notes: Annotated[str | None, Field(max_length=4_000)] = None
    tags: Annotated[list[str], Field(max_length=20)] = []


class VocabularyFromSelectionInput(StrictModel):
    term: Annotated[str, Field(min_length=1, max_length=200)]
    selection: SelectionInput
    definition: Annotated[str | None, Field(max_length=4_000)] = None
    part_of_speech: Annotated[str | None, Field(max_length=80)] = None
    pronunciation: Annotated[str | None, Field(max_length=160)] = None
    examples: Annotated[list[str], Field(max_length=20)] = []
    notes: Annotated[str | None, Field(max_length=4_000)] = None
    tags: Annotated[list[str], Field(max_length=20)] = []
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)]


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

