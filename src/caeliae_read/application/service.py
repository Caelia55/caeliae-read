from __future__ import annotations

from collections.abc import AsyncIterable
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import unicodedata
from uuid import UUID, uuid4
from datetime import UTC, datetime

from pypdf import PdfReader

from caeliae_read.config import Settings, get_settings
from caeliae_read.domain.models import Annotation, Checkpoint, Paper, ReadingContext, Selection
from caeliae_read.storage.repository import (
    CaeliaeReadRepository,
    IdempotencyConflictError,
    NotFoundError,
    RepositoryError,
    RevisionConflictError,
)

USER_ANNOTATION_STYLE_KEYS = frozenset({"primary", "secondary", "tertiary"})
USER_ANNOTATION_MARK_TYPES = frozenset({"underline", "highlight"})


class CaeliaeReadError(RuntimeError):
    code = "coread_error"


class ValidationError(CaeliaeReadError):
    code = "validation_error"


class PaperNotFoundError(CaeliaeReadError):
    code = "paper_not_found"


class SessionNotFoundError(CaeliaeReadError):
    code = "session_not_found"


class EventConflictError(CaeliaeReadError):
    code = "client_event_conflict"


class ReadingNotFoundError(CaeliaeReadError):
    code = "reading_not_found"


class ReadingConflictError(CaeliaeReadError):
    code = "reading_conflict"

    def __init__(self, message: str, current_revision: int | None = None, received_revision: int | None = None):
        super().__init__(message)
        self.current_revision = current_revision
        self.received_revision = received_revision


class ReadingValidationError(CaeliaeReadError):
    code = "reading_validation_error"


class CheckpointConflictError(CaeliaeReadError):
    code = "checkpoint_conflict"

    def __init__(self, current_revision: int, received_revision: int):
        super().__init__("checkpoint revision is stale or out of sequence")
        self.current_revision = current_revision
        self.received_revision = received_revision


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def require_uuid(value: str, field: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError) as error:
        raise ValidationError(f"{field} must be a UUID") from error


def safe_original_filename(value: str) -> str:
    name = unicodedata.normalize("NFC", (value or "").strip())
    if not name or len(name) > 255 or name in {".", ".."}:
        raise ValidationError("filename is invalid")
    if "/" in name or "\\" in name or Path(name).name != name:
        raise ValidationError("filename must not contain a path")
    return name


class CaeliaeReadService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.settings.papers_root.mkdir(parents=True, exist_ok=True)
        self.settings.temp_root.mkdir(parents=True, exist_ok=True)
        self.repository = CaeliaeReadRepository(self.settings.database_path)

    def _paper_path(self, storage_key: str) -> Path:
        if Path(storage_key).name != storage_key:
            raise CaeliaeReadError("invalid internal storage key")
        root = self.settings.papers_root.resolve()
        candidate = (root / storage_key).resolve()
        if candidate.parent != root:
            raise CaeliaeReadError("paper path escaped storage root")
        return candidate

    async def upload_pdf(
        self,
        *,
        original_filename: str,
        content_type: str,
        chunks: AsyncIterable[bytes],
    ) -> tuple[Paper, bool]:
        filename = safe_original_filename(original_filename)
        if content_type.lower().split(";", 1)[0].strip() != "application/pdf":
            raise ValidationError("content type must be application/pdf")

        temporary_path = self.settings.temp_root / f"{uuid4().hex}.upload"
        digest = hashlib.sha256()
        size = 0
        prefix = bytearray()
        try:
            with temporary_path.open("xb") as output:
                async for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        raise ValidationError("upload chunks must be bytes")
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > self.settings.max_pdf_bytes:
                        raise ValidationError("PDF exceeds the configured size limit")
                    if len(prefix) < 5:
                        prefix.extend(chunk[: 5 - len(prefix)])
                    digest.update(chunk)
                    output.write(chunk)
            if size == 0:
                raise ValidationError("PDF is empty")
            if bytes(prefix) != b"%PDF-":
                raise ValidationError("file does not have a PDF signature")
            try:
                # Many valid PDFs produced by browsers and desktop readers contain
                # recoverable xref/EOF irregularities. pypdf's non-strict parser
                # repairs those references while still requiring a readable PDF
                # structure and at least one page below.
                reader = PdfReader(str(temporary_path), strict=False)
                page_count = len(reader.pages)
            except Exception as error:
                raise ValidationError("PDF structure could not be validated") from error
            if page_count < 1:
                raise ValidationError("PDF has no pages")

            content_sha256 = digest.hexdigest()
            existing = self.repository.find_paper_by_hash(content_sha256)
            if existing:
                return existing[0], True

            paper_id = str(uuid4())
            storage_key = f"{uuid4().hex}.pdf"
            final_path = self._paper_path(storage_key)
            os.replace(temporary_path, final_path)
            try:
                paper = self.repository.add_paper(
                    paper_id=paper_id,
                    content_sha256=content_sha256,
                    original_filename=filename,
                    storage_key=storage_key,
                    size_bytes=size,
                    page_count=page_count,
                    created_at=utc_now(),
                )
            except sqlite3.IntegrityError:
                final_path.unlink(missing_ok=True)
                existing = self.repository.find_paper_by_hash(content_sha256)
                if existing is None:
                    raise
                return existing[0], True
            return paper, False
        finally:
            temporary_path.unlink(missing_ok=True)

    def list_papers(self, limit: int = 50) -> list[dict[str, object]]:
        if not 1 <= limit <= 100:
            raise ValidationError("limit must be between 1 and 100")
        return [paper.public_dict() for paper in self.repository.list_papers(limit)]

    def get_paper(self, paper_id: str) -> Paper:
        try:
            return self.repository.get_paper(require_uuid(paper_id, "paper_id"))[0]
        except NotFoundError as error:
            raise PaperNotFoundError("paper not found") from error

    def get_paper_file(self, paper_id: str) -> tuple[Paper, Path]:
        try:
            paper, storage_key = self.repository.get_paper(require_uuid(paper_id, "paper_id"))
        except NotFoundError as error:
            raise PaperNotFoundError("paper not found") from error
        path = self._paper_path(storage_key)
        if not path.is_file():
            raise PaperNotFoundError("paper file not found")
        return paper, path

    def create_session(self, paper_id: str) -> dict[str, str]:
        paper_id = require_uuid(paper_id, "paper_id")
        session_id = str(uuid4())
        try:
            self.repository.create_session(
                session_id=session_id, paper_id=paper_id, created_at=utc_now()
            )
        except NotFoundError as error:
            raise PaperNotFoundError("paper not found") from error
        return {"paper_id": paper_id, "session_id": session_id}

    def save_reading_state(self, payload: dict[str, object]) -> dict[str, object]:
        paper_id = require_uuid(str(payload["paper_id"]), "paper_id")
        session_id = require_uuid(str(payload["session_id"]), "session_id")
        client_event_id = require_uuid(str(payload["client_event_id"]), "client_event_id")
        page_number = int(payload["page_number"])
        revision = int(payload["revision"])
        page_text = str(payload["page_text"])
        if page_number < 1:
            raise ValidationError("page_number must be at least 1")
        if revision < 1:
            raise ValidationError("revision must be at least 1")
        if len(page_text) > self.settings.max_page_text_chars:
            raise ValidationError(
                f"page_text exceeds {self.settings.max_page_text_chars} characters"
            )

        selection = payload.get("selection")
        if selection is not None:
            if not isinstance(selection, dict):
                raise ValidationError("selection must be an object or null")
            if str(selection.get("paper_id")) != paper_id or str(selection.get("session_id")) != session_id:
                raise ValidationError("selection identity must match the state")
            if int(selection.get("page_number", 0)) != page_number:
                raise ValidationError("selection page must match the current page")
            if int(selection.get("revision", 0)) != revision:
                raise ValidationError("selection revision must match checkpoint revision")

        canonical = {
            "paper_id": paper_id,
            "session_id": session_id,
            "client_event_id": client_event_id,
            "page_number": page_number,
            "page_text": page_text,
            "revision": revision,
            "selection": selection,
        }
        payload_sha256 = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        try:
            response, replayed = self.repository.save_state(
                client_event_id=client_event_id,
                payload_sha256=payload_sha256,
                paper_id=paper_id,
                session_id=session_id,
                page_number=page_number,
                page_text=page_text,
                revision=revision,
                selection=selection,
                updated_at=utc_now(),
            )
        except IdempotencyConflictError as error:
            raise EventConflictError(str(error)) from error
        except RevisionConflictError as error:
            raise CheckpointConflictError(
                error.current_revision, error.received_revision
            ) from error
        except NotFoundError as error:
            raise SessionNotFoundError("session not found") from error
        except RepositoryError as error:
            raise ValidationError(str(error)) from error
        response["replayed"] = replayed
        return response

    def get_reading_context(self, session_id: str) -> ReadingContext:
        try:
            return self.repository.get_context(require_uuid(session_id, "session_id"))
        except NotFoundError as error:
            raise SessionNotFoundError("reading state not found") from error

    def get_current_reading_context(self) -> ReadingContext | None:
        return self.repository.get_current_context()

    def get_current_selection(self) -> Selection | None:
        context = self.get_current_reading_context()
        return context.selection if context else None

    def get_checkpoint(self) -> Checkpoint | None:
        context = self.get_current_reading_context()
        return context.checkpoint if context else None

    @staticmethod
    def _vocabulary_values(payload: dict[str, object], selection: dict[str, object] | None = None) -> dict[str, object]:
        term = unicodedata.normalize("NFC", str(payload.get("term", "")).strip())
        if not term or len(term) > 200:
            raise ValidationError("term must contain 1 to 200 characters")
        examples = payload.get("examples", [])
        tags = payload.get("tags", [])
        if not isinstance(examples, list) or len(examples) > 20 or any(not isinstance(item, str) for item in examples):
            raise ValidationError("examples must be a list of at most 20 strings")
        if not isinstance(tags, list) or len(tags) > 20 or any(not isinstance(item, str) for item in tags):
            raise ValidationError("tags must be a list of at most 20 strings")
        source_paper_id = payload.get("source_paper_id")
        source_session_id = payload.get("source_session_id")
        source_page_number = payload.get("source_page_number")
        if selection:
            source_paper_id = selection["paper_id"]
            source_session_id = selection["session_id"]
            source_page_number = selection["page_number"]
        if source_paper_id is not None:
            source_paper_id = require_uuid(str(source_paper_id), "source_paper_id")
        if source_session_id is not None:
            source_session_id = require_uuid(str(source_session_id), "source_session_id")
        if (source_paper_id is None) != (source_session_id is None):
            raise ValidationError("source_paper_id and source_session_id must be provided together")
        if source_page_number is not None:
            source_page_number = int(source_page_number)
            if source_page_number < 1:
                raise ValidationError("source_page_number must be at least 1")
        return {
            "term": term, "normalized_term": term.casefold(),
            "definition": None if payload.get("definition") is None else str(payload["definition"]).strip() or None,
            "part_of_speech": None if payload.get("part_of_speech") is None else str(payload["part_of_speech"]).strip() or None,
            "pronunciation": None if payload.get("pronunciation") is None else str(payload["pronunciation"]).strip() or None,
            "examples": [item.strip() for item in examples if item.strip()],
            "notes": None if payload.get("notes") is None else str(payload["notes"]).strip() or None,
            "tags": [item.strip() for item in tags if item.strip()],
            "source_paper_id": source_paper_id, "source_session_id": source_session_id,
            "source_page_number": source_page_number, "source_selection": selection,
        }

    def _vocabulary_public_dict(self, entry: dict[str, object] | object) -> dict[str, object]:
        values = entry.public_dict() if hasattr(entry, "public_dict") else dict(entry)  # type: ignore[arg-type]
        source_paper_id = values.get("source_paper_id")
        if source_paper_id:
            try:
                values["source_paper_title"] = self.repository.get_paper(str(source_paper_id))[0].original_filename
            except NotFoundError:
                values["source_paper_title"] = None
        else:
            values["source_paper_title"] = None
        return values

    def list_vocabulary(self, search: str | None = None, paper_id: str | None = None) -> list[dict[str, object]]:
        target = require_uuid(paper_id, "paper_id") if paper_id else None
        return [self._vocabulary_public_dict(entry) for entry in self.repository.list_vocabulary(search=search, paper_id=target)]

    def create_vocabulary(self, payload: dict[str, object], selection: dict[str, object] | None = None) -> dict[str, object]:
        values = self._vocabulary_values(payload, selection)
        if values["source_session_id"]:
            try:
                if self.repository.get_session_paper_id(str(values["source_session_id"])) != values["source_paper_id"]:
                    raise ValidationError("source session does not belong to source paper")
            except NotFoundError as error:
                raise SessionNotFoundError("source session not found") from error
        key = str(payload.get("idempotency_key", ""))
        entry = {"vocabulary_id": str(uuid4()), **values}
        canonical = json.dumps({k: v for k, v in payload.items() if k != "idempotency_key"}, sort_keys=True, separators=(",", ":"), default=str)
        response = {"entry": self._vocabulary_public_dict(entry), "created": True, "replayed": False}
        try:
            result, replayed = self.repository.save_vocabulary(entry=entry, idempotency_key=key, payload_sha256=hashlib.sha256(canonical.encode()).hexdigest(), response_json=json.dumps(response, ensure_ascii=False, separators=(",", ":")), created_at=utc_now())
        except IdempotencyConflictError as error:
            raise EventConflictError(str(error)) from error
        result["entry"] = self._vocabulary_public_dict(result["entry"])
        result["replayed"] = replayed
        return result

    def update_vocabulary(self, vocabulary_id: str, payload: dict[str, object]) -> dict[str, object]:
        target = require_uuid(vocabulary_id, "vocabulary_id")
        try:
            updated = self.repository.update_vocabulary(target, self._vocabulary_values(payload), utc_now())
        except NotFoundError as error:
            raise ValidationError(str(error)) from error
        return {"entry": updated.public_dict()}

    def delete_vocabulary(self, vocabulary_id: str) -> bool:
        return self.repository.delete_vocabulary(require_uuid(vocabulary_id, "vocabulary_id"))

    def export_vocabulary(self, search: str | None = None, paper_id: str | None = None) -> list[dict[str, object]]:
        return self.list_vocabulary(search, paper_id)

    def record_vocabulary_review(self, payload: dict[str, object]) -> dict[str, object]:
        vocabulary_id = require_uuid(str(payload.get("vocabulary_id")), "vocabulary_id")
        review_session_id = require_uuid(str(payload.get("review_session_id")), "review_session_id")
        rating = str(payload.get("rating"))
        if rating not in {"unknown", "fuzzy", "known"}:
            raise ValidationError("rating must be unknown, fuzzy, or known")
        mode = str(payload.get("mode") or "local")
        if mode not in {"local", "agent"}:
            raise ValidationError("mode must be local or agent")
        try:
            result = self.repository.add_vocabulary_review(review_id=str(uuid4()), vocabulary_id=vocabulary_id, review_session_id=review_session_id, rating=rating, mode=mode, reviewed_at=utc_now())
        except NotFoundError as error:
            raise ValidationError(str(error)) from error
        return {"review": result.public_dict()}

    @staticmethod
    def _reading_question_values(question: dict[str, object], now: str) -> dict[str, object]:
        question_type = str(question.get("question_type"))
        prompt = unicodedata.normalize("NFC", str(question.get("prompt", "")).strip())
        if question_type not in {"short_text", "single_choice"}:
            raise ReadingValidationError("question_type must be short_text or single_choice")
        if not prompt or len(prompt) > 2_000:
            raise ReadingValidationError("question prompt must contain 1 to 2000 characters")
        raw_choices = question.get("choices")
        choices: list[dict[str, str]] | None = None
        if question_type == "single_choice":
            if not isinstance(raw_choices, list) or not raw_choices:
                raise ReadingValidationError("single_choice questions require choices")
            choices = []
            seen: set[str] = set()
            for raw in raw_choices:
                if not isinstance(raw, dict):
                    raise ReadingValidationError("choices must contain id and value")
                choice_id = str(raw.get("id", "")).strip()
                value = str(raw.get("value", "")).strip()
                if not choice_id or not value or choice_id in seen:
                    raise ReadingValidationError("choice ids and values must be valid and unique")
                seen.add(choice_id)
                choices.append({"id": choice_id, "value": value})
        elif raw_choices:
            raise ReadingValidationError("short_text questions must not include choices")
        return {
            "question_id": str(uuid4()),
            "ordinal": int(question["ordinal"]),
            "question_type": question_type,
            "prompt": prompt,
            "choices": choices,
            "reference_answer": None if question.get("reference_answer") is None else str(question["reference_answer"]).strip() or None,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _reading_uuid(value: str, field: str) -> str:
        try:
            return require_uuid(value, field)
        except ValidationError as error:
            raise ReadingValidationError(str(error)) from error

    def _reading_task_detail(self, task_id: str) -> dict[str, object]:
        target = self._reading_uuid(task_id, "task_id")
        try:
            task = self.repository.get_reading_task(target)
        except NotFoundError as error:
            raise ReadingNotFoundError("reading task not found") from error
        paper = self.get_paper(str(task["paper_id"])).public_dict()
        return {
            "task": task,
            "paper": paper,
            "questions": self.repository.get_reading_questions(str(task["task_id"])),
            "submission": self.repository.get_reading_submission(str(task["task_id"])),
            "feedback": self.repository.list_reading_feedback(str(task["task_id"])),
        }

    def list_reading_tasks(self, status: str | None = None) -> list[dict[str, object]]:
        if status is not None and status not in {"draft", "active", "archived"}:
            raise ReadingValidationError("status must be draft, active, or archived")
        return self.repository.list_reading_tasks(status)

    def list_agent_reading_tasks(self, status: str | None = None) -> list[dict[str, object]]:
        return self.list_reading_tasks(status)

    def create_reading_task(self, payload: dict[str, object]) -> dict[str, object]:
        try:
            paper_id = require_uuid(str(payload["paper_id"]), "paper_id")
        except KeyError as error:
            raise ReadingValidationError("paper_id is required") from error
        title = unicodedata.normalize("NFC", str(payload.get("title", "")).strip())
        instructions = unicodedata.normalize("NFC", str(payload.get("instructions", "")).strip())
        status = str(payload.get("status") or "active")
        origin_kind = str(payload.get("origin_kind") or "manual")
        if not title or len(title) > 200:
            raise ReadingValidationError("title must contain 1 to 200 characters")
        if len(instructions) > 4_000:
            raise ReadingValidationError("instructions are too long")
        if status not in {"draft", "active", "archived"}:
            raise ReadingValidationError("status must be draft, active, or archived")
        if origin_kind not in {"manual", "agent", "daily"}:
            raise ReadingValidationError("origin_kind must be manual, agent, or daily")
        raw_questions = payload.get("questions")
        if not isinstance(raw_questions, list) or not raw_questions:
            raise ReadingValidationError("at least one question is required")
        now = utc_now()
        questions = [self._reading_question_values(question, now) for question in raw_questions if isinstance(question, dict)]
        if len(questions) != len(raw_questions):
            raise ReadingValidationError("questions must be objects")
        ordinals = [int(question["ordinal"]) for question in questions]
        if len(ordinals) != len(set(ordinals)):
            raise ReadingValidationError("question ordinals must be unique")
        if any(ordinal < 1 for ordinal in ordinals):
            raise ReadingValidationError("question ordinal must be at least 1")
        task = {
            "task_id": str(uuid4()),
            "paper_id": paper_id,
            "title": title,
            "instructions": instructions,
            "status": status,
            "origin_kind": origin_kind,
            "origin_ref": None if payload.get("origin_ref") is None else str(payload["origin_ref"]).strip() or None,
            "due_at": None if payload.get("due_at") is None else str(payload["due_at"]).strip() or None,
            "created_at": now,
            "updated_at": now,
        }
        question_public = [{**question, "task_id": task["task_id"]} for question in questions]
        response = {
            "task": task,
            "paper": self.get_paper(paper_id).public_dict(),
            "questions": question_public,
            "submission": None,
            "feedback": [],
            "replayed": False,
        }
        key = str(payload.get("idempotency_key") or "")
        canonical = {k: v for k, v in payload.items() if k != "idempotency_key"}
        payload_sha256 = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
        try:
            result, replayed = self.repository.save_reading_task(task=task, questions=question_public, idempotency_key=key, payload_sha256=payload_sha256, response_json=json.dumps(response, ensure_ascii=False, separators=(",", ":")))
        except IdempotencyConflictError as error:
            raise EventConflictError(str(error)) from error
        except NotFoundError as error:
            raise PaperNotFoundError("paper not found") from error
        result["replayed"] = replayed
        return result

    def get_reading_task(self, task_id: str) -> dict[str, object]:
        return self._reading_task_detail(task_id)

    def get_agent_reading_task(self, task_id: str) -> dict[str, object]:
        detail = self._reading_task_detail(task_id)
        return {
            "task": detail["task"],
            "paper": detail["paper"],
            "questions": detail["questions"],
        }

    def create_agent_reading_task(self, payload: dict[str, object]) -> dict[str, object]:
        agent_payload = dict(payload)
        agent_payload["origin_kind"] = "agent"
        agent_payload["status"] = "active"
        created = self.create_reading_task(agent_payload)
        return {
            "task": created["task"],
            "paper": created["paper"],
            "questions": created["questions"],
            "replayed": created["replayed"],
        }

    def open_reading_task(self, task_id: str) -> dict[str, object]:
        target = self._reading_uuid(task_id, "task_id")
        try:
            session_id = self.repository.open_reading_task(target, str(uuid4()), utc_now())
        except NotFoundError as error:
            raise ReadingNotFoundError("reading task not found") from error
        detail = self._reading_task_detail(target)
        detail.update({"session_id": session_id, "mode": "reading", "read_only": detail["task"]["status"] == "archived"})  # type: ignore[index]
        return detail

    def get_reading_submission(self, task_id: str) -> dict[str, object]:
        target = self._reading_uuid(task_id, "task_id")
        try:
            self.repository.get_reading_task(target)
        except NotFoundError as error:
            raise ReadingNotFoundError("reading task not found") from error
        return {"submission": self.repository.get_reading_submission(target)}

    def get_agent_reading_submission(self, task_id: str) -> dict[str, object]:
        detail = self._reading_task_detail(task_id)
        feedback_history = detail["feedback"]
        return {
            "task": detail["task"],
            "paper": detail["paper"],
            "questions": detail["questions"],
            "submission": detail["submission"],
            "feedback_history": feedback_history,
            "latest_feedback": feedback_history[-1] if feedback_history else None,
        }

    @staticmethod
    def _reading_answer_values(task_questions: list[dict[str, object]], raw_answers: object) -> list[dict[str, object]]:
        if not isinstance(raw_answers, list):
            raise ReadingValidationError("answers must be a list")
        question_map = {str(question["question_id"]): question for question in task_questions}
        values: list[dict[str, object]] = []
        seen: set[str] = set()
        for raw in raw_answers:
            if not isinstance(raw, dict):
                raise ReadingValidationError("answers must contain objects")
            try:
                question_id = require_uuid(str(raw.get("question_id")), "question_id")
            except (ValidationError, TypeError) as error:
                raise ReadingValidationError("question_id is invalid") from error
            if question_id in seen or question_id not in question_map:
                raise ReadingValidationError("answer question does not belong to reading task")
            seen.add(question_id)
            question = question_map[question_id]
            answer_text = None if raw.get("answer_text") is None else str(raw.get("answer_text")).strip() or None
            selected_choice = None if raw.get("selected_choice") is None else str(raw.get("selected_choice")).strip() or None
            if question["question_type"] == "short_text":
                if selected_choice is not None:
                    raise ReadingValidationError("short_text answers use answer_text")
            else:
                if answer_text is not None:
                    raise ReadingValidationError("single_choice answers use selected_choice")
                choices = question.get("choices") or []
                choice_ids = {str(choice["id"]) for choice in choices}  # type: ignore[index]
                if selected_choice is not None and selected_choice not in choice_ids:
                    raise ReadingValidationError("selected_choice is not valid for question")
            values.append({"question_id": question_id, "answer_text": answer_text, "selected_choice": selected_choice})
        return values

    def save_reading_draft(self, task_id: str, payload: dict[str, object]) -> dict[str, object]:
        target = self._reading_uuid(task_id, "task_id")
        try:
            questions = self.repository.get_reading_questions(target)
            self.repository.get_reading_task(target)
        except NotFoundError as error:
            raise ReadingNotFoundError("reading task not found") from error
        answers = self._reading_answer_values(questions, payload.get("answers", []))
        client_event_id = require_uuid(str(payload["client_event_id"]), "client_event_id")
        expected_revision = int(payload.get("revision", 0))
        canonical = {"task_id": target, "revision": expected_revision, "answers": answers, "client_event_id": client_event_id}
        digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
        try:
            result, replayed = self.repository.save_reading_draft(task_id=target, submission_id=str(uuid4()), expected_revision=expected_revision, answers=answers, client_event_id=client_event_id, payload_sha256=digest, now=utc_now())
        except IdempotencyConflictError as error:
            raise EventConflictError(str(error)) from error
        except RevisionConflictError as error:
            raise ReadingConflictError("reading submission revision is stale", error.current_revision, error.received_revision) from error
        except NotFoundError as error:
            raise ReadingNotFoundError("reading task not found") from error
        except RepositoryError as error:
            message = str(error)
            if "read-only" in message or "submitted" in message:
                raise ReadingConflictError(message) from error
            raise ReadingValidationError(message) from error
        result["replayed"] = replayed
        return result

    def submit_reading(self, task_id: str, payload: dict[str, object]) -> dict[str, object]:
        target = self._reading_uuid(task_id, "task_id")
        try:
            self.repository.get_reading_task(target)
        except NotFoundError as error:
            raise ReadingNotFoundError("reading task not found") from error
        expected_revision = int(payload["revision"])
        client_event_id = require_uuid(str(payload["client_event_id"]), "client_event_id")
        canonical = {"task_id": target, "revision": expected_revision, "client_event_id": client_event_id}
        digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
        try:
            result, replayed = self.repository.submit_reading(task_id=target, expected_revision=expected_revision, client_event_id=client_event_id, payload_sha256=digest, now=utc_now())
        except IdempotencyConflictError as error:
            raise EventConflictError(str(error)) from error
        except RevisionConflictError as error:
            raise ReadingConflictError("reading submission revision is stale", error.current_revision, error.received_revision) from error
        except NotFoundError as error:
            raise ReadingNotFoundError("reading task or submission not found") from error
        except RepositoryError as error:
            message = str(error)
            if "already submitted" in message or "read-only" in message:
                raise ReadingConflictError(message) from error
            raise ReadingValidationError(message) from error
        result["replayed"] = replayed
        return result

    def list_reading_feedback(self, task_id: str) -> dict[str, object]:
        target = self._reading_uuid(task_id, "task_id")
        try:
            self.repository.get_reading_task(target)
        except NotFoundError as error:
            raise ReadingNotFoundError("reading task not found") from error
        return {"feedback": self.repository.list_reading_feedback(target)}

    def _normalize_reading_feedback(
        self,
        task_id: str,
        feedback_text: object,
        question_feedback: object,
    ) -> tuple[str, dict[str, str] | None]:
        normalized_text = str(feedback_text).strip()
        if not normalized_text or len(normalized_text) > 4_000:
            raise ReadingValidationError("feedback_text must contain 1 to 4000 characters")
        if question_feedback is not None and not isinstance(question_feedback, dict):
            raise ReadingValidationError("question_feedback must be an object")
        question_map = {str(question["question_id"]): question for question in self.repository.get_reading_questions(task_id)}
        normalized_feedback: dict[str, str] | None = None
        if question_feedback is not None:
            normalized_feedback = {}
            for key, value in question_feedback.items():
                if key not in question_map:
                    raise ReadingValidationError("question feedback references another task")
                text = str(value).strip()
                if not text or len(text) > 2_000:
                    raise ReadingValidationError("question feedback text is invalid")
                normalized_feedback[key] = text
        return normalized_text, normalized_feedback

    def add_reading_feedback(self, task_id: str, payload: dict[str, object]) -> dict[str, object]:
        target = self._reading_uuid(task_id, "task_id")
        source_kind = str(payload.get("source_kind"))
        if source_kind not in {"manual", "agent"}:
            raise ReadingValidationError("source_kind must be manual or agent")
        feedback_text, normalized_feedback = self._normalize_reading_feedback(
            target, payload.get("feedback_text", ""), payload.get("question_feedback")
        )
        key = None if payload.get("idempotency_key") is None else str(payload["idempotency_key"])
        canonical = {"task_id": target, "source_kind": source_kind, "feedback_text": feedback_text, "question_feedback": normalized_feedback}
        digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest() if key else None
        try:
            result, replayed = self.repository.add_reading_feedback(task_id=target, feedback_id=str(uuid4()), feedback_text=feedback_text, question_feedback_json=json.dumps(normalized_feedback, ensure_ascii=False, separators=(",", ":")) if normalized_feedback else None, source_kind=source_kind, idempotency_key=key, payload_sha256=digest, now=utc_now())
        except IdempotencyConflictError as error:
            raise EventConflictError(str(error)) from error
        except NotFoundError as error:
            raise ReadingNotFoundError("reading task or submission not found") from error
        except RepositoryError as error:
            message = str(error)
            if "requires" in message:
                raise ReadingConflictError(message) from error
            raise ReadingValidationError(message) from error
        result["replayed"] = replayed
        return result

    def create_agent_reading_feedback(
        self,
        task_id: str,
        *,
        submission_id: str,
        expected_submission_revision: int,
        feedback_text: str,
        question_feedback: dict[str, str] | None,
        idempotency_key: str,
    ) -> dict[str, object]:
        target = self._reading_uuid(task_id, "task_id")
        submission_target = self._reading_uuid(submission_id, "submission_id")
        if expected_submission_revision < 1:
            raise ReadingValidationError("expected_submission_revision must be at least 1")
        if not 8 <= len(idempotency_key) <= 128:
            raise ReadingValidationError("idempotency_key length invalid")
        normalized_text, normalized_feedback = self._normalize_reading_feedback(
            target, feedback_text, question_feedback
        )
        canonical = {
            "task_id": target,
            "submission_id": submission_target,
            "expected_submission_revision": expected_submission_revision,
            "feedback_text": normalized_text,
            "question_feedback": normalized_feedback,
            "source_kind": "agent",
        }
        digest = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        try:
            result, replayed = self.repository.add_reading_feedback(
                task_id=target,
                feedback_id=str(uuid4()),
                feedback_text=normalized_text,
                question_feedback_json=json.dumps(normalized_feedback, ensure_ascii=False, separators=(",", ":")) if normalized_feedback else None,
                source_kind="agent",
                idempotency_key=idempotency_key,
                payload_sha256=digest,
                now=utc_now(),
                expected_submission_id=submission_target,
                expected_submission_revision=expected_submission_revision,
                reject_archived_task=True,
            )
        except IdempotencyConflictError as error:
            raise EventConflictError(str(error)) from error
        except RevisionConflictError as error:
            raise ReadingConflictError("reading submission revision is stale", error.current_revision, error.received_revision) from error
        except NotFoundError as error:
            raise ReadingNotFoundError("reading task or submission not found") from error
        except RepositoryError as error:
            message = str(error)
            if "read-only" in message or "identity mismatch" in message or "requires" in message:
                raise ReadingConflictError(message) from error
            raise ReadingValidationError(message) from error
        result["replayed"] = replayed
        result["submission_status"] = "reviewed"
        return result

    def archive_reading_task(self, task_id: str) -> dict[str, object]:
        try:
            return {"task": self.repository.archive_reading_task(self._reading_uuid(task_id, "task_id"), utc_now())}
        except NotFoundError as error:
            raise ReadingNotFoundError("reading task not found") from error

    def list_annotations(self, paper_id: str | None = None, page_number: int | None = None) -> list[dict[str, object]]:
        context = self.get_current_reading_context()
        target = require_uuid(paper_id, "paper_id") if paper_id else (context.paper.paper_id if context else None)
        if not target: return []
        return [a.public_dict() for a in self.repository.list_annotations(target, page_number)]

    def create_assistant_annotation(self, payload: dict[str, object]) -> dict[str, object]:
        required = ("paper_id", "session_id", "page_number", "exact_text", "prefix", "suffix", "normalized_quads", "page_width", "page_height", "rotation", "note", "remember", "idempotency_key")
        if any(k not in payload for k in required): raise ValidationError("all annotation fields and idempotency_key are required")
        paper_id = require_uuid(str(payload["paper_id"]), "paper_id"); session_id = require_uuid(str(payload["session_id"]), "session_id")
        context = self.get_reading_context(session_id)
        if context.paper.paper_id != paper_id or context.page_number != int(payload["page_number"]): raise ValidationError("annotation target does not match current reading context")
        key = str(payload["idempotency_key"])
        if not 8 <= len(key) <= 128: raise ValidationError("idempotency_key length invalid")
        annotation = {"annotation_id": str(uuid4()), "thread_id": str(payload.get("thread_id") or uuid4()), "paper_id": paper_id, "session_id": session_id, "page_number": int(payload["page_number"]), "exact_text": str(payload["exact_text"]), "prefix": str(payload["prefix"]), "suffix": str(payload["suffix"]), "normalized_quads": payload["normalized_quads"], "page_width": float(payload["page_width"]), "page_height": float(payload["page_height"]), "rotation": int(payload["rotation"]), "note": None if payload["note"] is None else str(payload["note"]), "remember": bool(payload["remember"]), "author": "assistant", "style_key": None, "mark_type": "underline"}
        canonical = json.dumps({k: v for k, v in payload.items() if k != "idempotency_key"}, sort_keys=True, separators=(",",":"), default=str)
        response = {"annotation": annotation, "created": True, "replayed": False}
        try: result, replayed = self.repository.save_annotation(annotation=annotation, idempotency_key=key, payload_sha256=hashlib.sha256(canonical.encode()).hexdigest(), response_json=json.dumps(response, separators=(",",":")), created_at=utc_now())
        except IdempotencyConflictError as error: raise EventConflictError(str(error)) from error
        result["replayed"] = replayed; return result

    def create_user_annotation(self, payload: dict[str, object]) -> dict[str, object]:
        paper_id = require_uuid(str(payload.get("paper_id")), "paper_id")
        session_id = require_uuid(str(payload.get("session_id")), "session_id")
        context = self.get_reading_context(session_id)
        if context.paper.paper_id != paper_id or context.page_number != int(payload.get("page_number", 0)):
            raise ValidationError("annotation target does not match current reading context")
        key = str(payload.get("idempotency_key") or "")
        if not 8 <= len(key) <= 128: raise ValidationError("idempotency_key length invalid")
        style_key = str(payload.get("style_key") or "primary")
        if style_key not in USER_ANNOTATION_STYLE_KEYS:
            raise ValidationError("style_key must be primary, secondary, or tertiary")
        mark_type = str(payload.get("mark_type") or "underline")
        if mark_type not in USER_ANNOTATION_MARK_TYPES:
            raise ValidationError("mark_type must be underline or highlight")
        annotation = {"annotation_id": str(uuid4()), "thread_id": str(payload.get("thread_id") or uuid4()), "paper_id": paper_id, "session_id": session_id, "page_number": int(payload["page_number"]), "exact_text": str(payload["exact_text"]), "prefix": str(payload.get("prefix", "")), "suffix": str(payload.get("suffix", "")), "normalized_quads": payload["normalized_quads"], "page_width": float(payload["page_width"]), "page_height": float(payload["page_height"]), "rotation": int(payload.get("rotation", 0)), "note": None if payload.get("note") is None else str(payload["note"]), "remember": False, "author": "user", "style_key": style_key, "mark_type": mark_type}
        canonical = json.dumps({k: v for k, v in payload.items() if k != "idempotency_key"}, sort_keys=True, separators=(",", ":"), default=str); response = {"annotation": annotation, "created": True, "replayed": False}
        try: result, replayed = self.repository.save_annotation(annotation=annotation, idempotency_key=key, payload_sha256=hashlib.sha256(canonical.encode()).hexdigest(), response_json=json.dumps(response, separators=(",", ":")), created_at=utc_now(), author="user")
        except IdempotencyConflictError as error: raise EventConflictError(str(error)) from error
        result["replayed"] = replayed; return result

    def revoke_assistant_remember(self, annotation_id: str) -> dict[str, object]:
        target_id = require_uuid(annotation_id, "annotation_id")
        annotation = self.repository.get_annotation(target_id)
        if annotation is None:
            raise ValidationError("annotation does not exist")
        if annotation.author != "assistant" or not annotation.remember:
            raise ValidationError("target must be an assistant annotation with remember enabled")
        try:
            updated = self.repository.revoke_assistant_remember(target_id, utc_now())
        except RepositoryError as error:
            raise ValidationError(str(error)) from error
        return {"annotation": updated.public_dict()}

    def update_user_annotation(self, annotation_id: str, payload: dict[str, object]) -> dict[str, object]:
        target_id = require_uuid(annotation_id, "annotation_id")
        session_id = require_uuid(str(payload.get("session_id")), "session_id")
        target = self.repository.get_annotation(target_id)
        if target is None or target.author != "user" or target.session_id != session_id:
            raise ValidationError("user annotation was not found")
        if "style_key" not in payload and "mark_type" not in payload and "note" not in payload:
            raise ValidationError("annotation update requires style_key, mark_type, or note")
        style_key = target.style_key
        if "style_key" in payload:
            style_key = str(payload.get("style_key") or "primary")
            if style_key not in USER_ANNOTATION_STYLE_KEYS:
                raise ValidationError("style_key must be primary, secondary, or tertiary")
        note = target.note
        if "note" in payload:
            note = None if payload.get("note") is None else str(payload["note"])
        mark_type = target.mark_type
        if "mark_type" in payload:
            mark_type = str(payload.get("mark_type") or "underline")
            if mark_type not in USER_ANNOTATION_MARK_TYPES:
                raise ValidationError("mark_type must be underline or highlight")
        try:
            updated = self.repository.update_user_annotation(
                target_id,
                session_id,
                style_key=style_key,
                mark_type=mark_type,
                note=note,
                updated_at=utc_now(),
            )
        except NotFoundError as error:
            raise ValidationError(str(error)) from error
        return {"annotation": updated.public_dict()}

    def list_sticky_notes(self, paper_id: str | None = None, page: int | None = None) -> list[dict[str, object]]:
        context = self.get_current_reading_context()
        target = require_uuid(paper_id, "paper_id") if paper_id else (context.paper.paper_id if context else None)
        if not target:
            return []
        return [note.public_dict() for note in self.repository.list_sticky_notes(target, page)]

    @staticmethod
    def _sticky_payload(payload: dict[str, object]) -> tuple[str, int, float, float, str, str]:
        paper_id = require_uuid(str(payload.get("paper_id")), "paper_id")
        try:
            page = int(payload.get("page", 0)); x = float(payload.get("x")); y = float(payload.get("y"))
        except (TypeError, ValueError) as error:
            raise ValidationError("sticky note page, x, and y are invalid") from error
        text = str(payload.get("text") or "").strip()
        style_key = str(payload.get("style_key") or "primary")
        if page < 1 or not 0 <= x <= 1 or not 0 <= y <= 1 or not text or len(text) > 2_000:
            raise ValidationError("sticky note coordinates or text are invalid")
        if style_key not in USER_ANNOTATION_STYLE_KEYS:
            raise ValidationError("style_key must be primary, secondary, or tertiary")
        return paper_id, page, x, y, text, style_key

    def create_sticky_note(self, payload: dict[str, object]) -> dict[str, object]:
        paper_id, page, x, y, text, style_key = self._sticky_payload(payload)
        paper = self.get_paper(paper_id)
        if page > paper.page_count:
            raise ValidationError("sticky note page is outside the paper")
        note = self.repository.insert_sticky_note({"id": str(uuid4()), "paper_id": paper_id, "page": page, "x": x, "y": y, "text": text, "style_key": style_key}, utc_now())
        return {"sticky_note": note.public_dict()}

    def update_sticky_note(self, note_id: str, payload: dict[str, object]) -> dict[str, object]:
        note_id = require_uuid(note_id, "id")
        existing = self.repository.get_sticky_note(note_id)
        if existing is None:
            raise ValidationError("sticky note was not found")
        text = str(payload.get("text") or "").strip(); style_key = str(payload.get("style_key") or "primary")
        if not text or len(text) > 2_000 or style_key not in USER_ANNOTATION_STYLE_KEYS:
            raise ValidationError("sticky note text or style_key is invalid")
        try:
            x = float(payload["x"]) if "x" in payload else existing.x
            y = float(payload["y"]) if "y" in payload else existing.y
        except (TypeError, ValueError) as error:
            raise ValidationError("sticky note coordinates are invalid") from error
        if not 0 <= x <= 1 or not 0 <= y <= 1:
            raise ValidationError("sticky note coordinates are invalid")
        try:
            note = self.repository.update_sticky_note(note_id, x=x, y=y, text=text, style_key=style_key, updated_at=utc_now())
        except NotFoundError as error:
            raise ValidationError(str(error)) from error
        return {"sticky_note": note.public_dict()}

    def delete_sticky_note(self, note_id: str) -> bool:
        try:
            return self.repository.delete_sticky_note(require_uuid(note_id, "id"))
        except NotFoundError as error:
            raise ValidationError(str(error)) from error

    def list_summary_notes(self, paper_id: str, page_number: int | None = None) -> list[dict[str, object]]:
        target = require_uuid(paper_id, "paper_id")
        paper = self.get_paper(target)
        if page_number is not None and (page_number < 1 or page_number > paper.page_count):
            raise ValidationError("summary note page is outside the paper")
        return [note.public_dict() for note in self.repository.list_summary_notes(target, page_number)]

    @staticmethod
    def _summary_note_payload(payload: dict[str, object]) -> tuple[str, int, float, str]:
        paper_id = require_uuid(str(payload.get("paper_id")), "paper_id")
        try:
            page_number = int(payload.get("page_number", 0))
            normalized_y = float(payload.get("normalized_y"))
        except (TypeError, ValueError) as error:
            raise ValidationError("summary note page and position are invalid") from error
        text = str(payload.get("text") or "").strip()
        if page_number < 1 or not 0 <= normalized_y <= 1 or not text or len(text) > 2_000:
            raise ValidationError("summary note page, position, or text is invalid")
        return paper_id, page_number, normalized_y, text

    def create_summary_note(self, payload: dict[str, object]) -> dict[str, object]:
        paper_id, page_number, normalized_y, text = self._summary_note_payload(payload)
        paper = self.get_paper(paper_id)
        if page_number > paper.page_count:
            raise ValidationError("summary note page is outside the paper")
        note = self.repository.insert_summary_note(
            {
                "summary_note_id": str(uuid4()),
                "paper_id": paper_id,
                "page_number": page_number,
                "normalized_y": normalized_y,
                "text": text,
            },
            utc_now(),
        )
        return {"summary_note": note.public_dict()}

    def update_summary_note(self, summary_note_id: str, payload: dict[str, object]) -> dict[str, object]:
        target_id = require_uuid(summary_note_id, "summary_note_id")
        existing = self.repository.get_summary_note(target_id)
        if existing is None:
            raise ValidationError("summary note was not found")
        text = str(payload.get("text") or "").strip()
        if not text or len(text) > 2_000:
            raise ValidationError("summary note text is invalid")
        try:
            note = self.repository.update_summary_note(target_id, text=text, updated_at=utc_now())
        except NotFoundError as error:
            raise ValidationError(str(error)) from error
        return {"summary_note": note.public_dict()}

    def delete_summary_note(self, summary_note_id: str) -> bool:
        target_id = require_uuid(summary_note_id, "summary_note_id")
        return self.repository.delete_summary_note(target_id)


@lru_cache(maxsize=1)
def get_service() -> CaeliaeReadService:
    return CaeliaeReadService()
