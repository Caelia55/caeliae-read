from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PaperOutput(PublicModel):
    paper_id: UUID
    original_filename: str
    size_bytes: int = Field(gt=0)
    page_count: int = Field(gt=0)
    created_at: str


class NormalizedQuadOutput(PublicModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class SelectionOutput(PublicModel):
    paper_id: UUID
    session_id: UUID
    page_number: int = Field(ge=1)
    exact_text: str
    prefix: str
    suffix: str
    normalized_quads: list[NormalizedQuadOutput]
    page_width: float = Field(gt=0)
    page_height: float = Field(gt=0)
    rotation: Literal[0, 90, 180, 270]
    revision: int = Field(ge=1)


class CheckpointOutput(PublicModel):
    paper_id: UUID
    session_id: UUID
    page_number: int = Field(ge=1)
    revision: int = Field(ge=1)
    updated_at: str


class ReadingContextOutput(PublicModel):
    paper: PaperOutput
    session_id: UUID
    page_number: int = Field(ge=1)
    page_text: str = Field(max_length=4_000)
    checkpoint: CheckpointOutput
    selection: SelectionOutput | None


class PaperListOutput(PublicModel):
    papers: list[PaperOutput]


class SummaryNoteOutput(PublicModel):
    summary_note_id: UUID
    paper_id: UUID
    page_number: int = Field(ge=1)
    normalized_y: float = Field(ge=0, le=1)
    text: str = Field(min_length=1, max_length=2_000)
    created_at: str
    updated_at: str


class SummaryNoteListOutput(PublicModel):
    summary_notes: list[SummaryNoteOutput]


class ReadingMcpChoice(PublicModel):
    id: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=400)


class ReadingMcpQuestionInput(PublicModel):
    ordinal: int = Field(ge=1)
    question_type: Literal["short_text", "single_choice"]
    prompt: str = Field(min_length=1, max_length=2_000)
    choices: list[ReadingMcpChoice] | None = Field(default=None, max_length=32)
    reference_answer: str | None = Field(default=None, max_length=4_000)

    @model_validator(mode="after")
    def validate_choices(self) -> "ReadingMcpQuestionInput":
        if self.question_type == "short_text" and self.choices:
            raise ValueError("short_text questions must not include choices")
        if self.question_type == "single_choice":
            if not self.choices:
                raise ValueError("single_choice questions require choices")
            ids = [choice.id for choice in self.choices]
            if len(ids) != len(set(ids)):
                raise ValueError("choice ids must be unique")
        return self


class ReadingMcpTask(PublicModel):
    task_id: UUID
    paper_id: UUID
    title: str
    instructions: str
    status: Literal["draft", "active", "archived"]
    origin_kind: Literal["manual", "agent", "daily"]
    origin_ref: str | None
    due_at: str | None
    created_at: str
    updated_at: str


class ReadingMcpQuestion(PublicModel):
    question_id: UUID
    task_id: UUID
    ordinal: int = Field(ge=1)
    question_type: Literal["short_text", "single_choice"]
    prompt: str
    choices: list[ReadingMcpChoice] | None
    reference_answer: str | None
    created_at: str
    updated_at: str


class ReadingMcpTaskListItem(ReadingMcpTask):
    paper: PaperOutput
    submission_status: Literal["draft", "submitted", "reviewed"] | None
    questions_count: int = Field(ge=0)


class ReadingMcpTaskListOutput(PublicModel):
    tasks: list[ReadingMcpTaskListItem]


class ReadingMcpTaskOutput(PublicModel):
    task: ReadingMcpTask
    paper: PaperOutput
    questions: list[ReadingMcpQuestion]


class ReadingMcpSubmissionAnswer(PublicModel):
    question_id: UUID
    answer_text: str | None
    selected_choice: str | None
    created_at: str
    updated_at: str


class ReadingMcpSubmission(PublicModel):
    submission_id: UUID
    task_id: UUID
    status: Literal["draft", "submitted", "reviewed"]
    revision: int = Field(ge=1)
    submitted_at: str | None
    created_at: str
    updated_at: str
    answers: list[ReadingMcpSubmissionAnswer]


class ReadingMcpFeedback(PublicModel):
    feedback_id: UUID
    submission_id: UUID
    revision: int = Field(ge=1)
    feedback_text: str
    question_feedback: dict[str, str] | None
    source_kind: Literal["manual", "agent"]
    created_at: str


class ReadingMcpSubmissionOutput(PublicModel):
    task: ReadingMcpTask
    paper: PaperOutput
    questions: list[ReadingMcpQuestion]
    submission: ReadingMcpSubmission | None
    feedback_history: list[ReadingMcpFeedback]
    latest_feedback: ReadingMcpFeedback | None


class ReadingMcpTaskCreatedOutput(PublicModel):
    task: ReadingMcpTask
    paper: PaperOutput
    questions: list[ReadingMcpQuestion]
    replayed: bool


class ReadingMcpFeedbackCreatedOutput(PublicModel):
    feedback: ReadingMcpFeedback
    submission_status: Literal["reviewed"]
    replayed: bool
