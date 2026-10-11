from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Paper:
    paper_id: str
    original_filename: str
    size_bytes: int
    page_count: int
    created_at: str

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Checkpoint:
    paper_id: str
    session_id: str
    page_number: int
    revision: int
    updated_at: str

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Selection:
    paper_id: str
    session_id: str
    page_number: int
    exact_text: str
    prefix: str
    suffix: str
    normalized_quads: list[dict[str, float]]
    page_width: float
    page_height: float
    rotation: int
    revision: int

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReadingContext:
    paper: Paper
    session_id: str
    page_number: int
    page_text: str
    checkpoint: Checkpoint
    selection: Selection | None

    def public_dict(self) -> dict[str, Any]:
        return {
            "paper": self.paper.public_dict(),
            "session_id": self.session_id,
            "page_number": self.page_number,
            "page_text": self.page_text,
            "checkpoint": self.checkpoint.public_dict(),
            "selection": self.selection.public_dict() if self.selection else None,
        }


@dataclass(frozen=True)
class Annotation:
    annotation_id: str
    thread_id: str
    paper_id: str
    session_id: str
    page_number: int
    exact_text: str
    prefix: str
    suffix: str
    normalized_quads: list[dict[str, float]]
    page_width: float
    page_height: float
    rotation: int
    author: str
    note: str | None
    remember: bool
    style_key: str | None
    mark_type: str
    created_at: str
    updated_at: str

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StickyNote:
    id: str
    paper_id: str
    page: int
    x: float
    y: float
    text: str
    style_key: str
    created_at: str
    updated_at: str

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SummaryNote:
    summary_note_id: str
    paper_id: str
    page_number: int
    normalized_y: float
    text: str
    created_at: str
    updated_at: str

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VocabularyEntry:
    vocabulary_id: str
    term: str
    normalized_term: str
    definition: str | None
    part_of_speech: str | None
    pronunciation: str | None
    examples: list[str]
    notes: str | None
    tags: list[str]
    # Legacy source_* fields remain the initial/primary mirror; occurrences are canonical after v10.
    source_paper_id: str | None
    source_session_id: str | None
    source_page_number: int | None
    source_selection: dict[str, Any] | None
    created_at: str
    updated_at: str
    # V1 storage fields; public_dict intentionally preserves the pre-v10 API shape.
    lemma: str | None = None
    entry_type: str = "word"

    def public_dict(self) -> dict[str, Any]:
        return {
            "vocabulary_id": self.vocabulary_id,
            "term": self.term,
            "normalized_term": self.normalized_term,
            "definition": self.definition,
            "part_of_speech": self.part_of_speech,
            "pronunciation": self.pronunciation,
            "examples": self.examples,
            "notes": self.notes,
            "tags": self.tags,
            "source_paper_id": self.source_paper_id,
            "source_session_id": self.source_session_id,
            "source_page_number": self.source_page_number,
            "source_selection": self.source_selection,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class VocabularyOccurrence:
    occurrence_id: str
    vocabulary_id: str
    surface_form: str
    paper_id: str | None
    session_id: str | None
    page_number: int | None
    context_excerpt: str | None
    selection_json: str | None
    created_at: str

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VocabularyReviewResult:
    review_id: str
    vocabulary_id: str
    review_session_id: str
    rating: str
    mode: str
    reviewed_at: str

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)
