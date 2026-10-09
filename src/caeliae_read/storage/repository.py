from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from caeliae_read.domain.models import Annotation, Checkpoint, Paper, ReadingContext, Selection, StickyNote, SummaryNote, VocabularyEntry, VocabularyReviewResult
from caeliae_read.storage.migrations import migrate


class RepositoryError(RuntimeError):
    pass


class NotFoundError(RepositoryError):
    pass


class IdempotencyConflictError(RepositoryError):
    pass


class RevisionConflictError(RepositoryError):
    def __init__(self, current_revision: int, received_revision: int):
        super().__init__(
            f"checkpoint revision conflict: current={current_revision}, received={received_revision}"
        )
        self.current_revision = current_revision
        self.received_revision = received_revision


class CaeliaeReadRepository:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            migrate(connection)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _paper(row: sqlite3.Row) -> Paper:
        return Paper(
            paper_id=row["paper_id"],
            original_filename=row["original_filename"],
            size_bytes=row["size_bytes"],
            page_count=row["page_count"],
            created_at=row["created_at"],
        )

    def find_paper_by_hash(self, content_sha256: str) -> tuple[Paper, str] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM papers WHERE content_sha256 = ?", (content_sha256,)
            ).fetchone()
        return (self._paper(row), row["storage_key"]) if row else None

    def add_paper(
        self,
        *,
        paper_id: str,
        content_sha256: str,
        original_filename: str,
        storage_key: str,
        size_bytes: int,
        page_count: int,
        created_at: str,
    ) -> Paper:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO papers(
                    paper_id, content_sha256, original_filename, storage_key,
                    size_bytes, mime_type, page_count, created_at
                ) VALUES (?, ?, ?, ?, ?, 'application/pdf', ?, ?)
                """,
                (
                    paper_id,
                    content_sha256,
                    original_filename,
                    storage_key,
                    size_bytes,
                    page_count,
                    created_at,
                ),
            )
            connection.commit()
        return Paper(paper_id, original_filename, size_bytes, page_count, created_at)

    def get_paper(self, paper_id: str) -> tuple[Paper, str]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM papers WHERE paper_id = ?", (paper_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError("paper not found")
        return self._paper(row), row["storage_key"]

    def list_papers(self, limit: int) -> list[Paper]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM papers ORDER BY created_at DESC, paper_id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._paper(row) for row in rows]

    def create_session(
        self, *, session_id: str, paper_id: str, created_at: str
    ) -> str:
        with self.connection() as connection:
            if connection.execute(
                "SELECT 1 FROM papers WHERE paper_id = ?", (paper_id,)
            ).fetchone() is None:
                raise NotFoundError("paper not found")
            connection.execute(
                "INSERT INTO reading_sessions(session_id, paper_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, paper_id, created_at, created_at),
            )
            connection.commit()
        return session_id

    def get_session_paper_id(self, session_id: str) -> str:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT paper_id FROM reading_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError("session not found")
        return row["paper_id"]

    def save_state(
        self,
        *,
        client_event_id: str,
        payload_sha256: str,
        paper_id: str,
        session_id: str,
        page_number: int,
        page_text: str,
        revision: int,
        selection: dict[str, Any] | None,
        updated_at: str,
    ) -> tuple[dict[str, Any], bool]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            event = connection.execute(
                "SELECT payload_sha256, response_json FROM client_events WHERE client_event_id = ?",
                (client_event_id,),
            ).fetchone()
            if event:
                if event["payload_sha256"] != payload_sha256:
                    raise IdempotencyConflictError(
                        "client_event_id was already used with a different payload"
                    )
                connection.rollback()
                return json.loads(event["response_json"]), True

            session = connection.execute(
                """
                SELECT s.paper_id, p.page_count
                FROM reading_sessions s
                JOIN papers p ON p.paper_id = s.paper_id
                WHERE s.session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if session is None:
                raise NotFoundError("session not found")
            if session["paper_id"] != paper_id:
                raise RepositoryError("session does not belong to paper")
            if page_number > session["page_count"]:
                raise RepositoryError("page_number exceeds the paper page count")

            state = connection.execute(
                "SELECT revision FROM reading_states WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            current_revision = state["revision"] if state else 0
            if revision != current_revision + 1:
                raise RevisionConflictError(current_revision, revision)

            connection.execute(
                """
                INSERT INTO reading_states(
                    session_id, paper_id, page_number, page_text, revision, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    paper_id=excluded.paper_id,
                    page_number=excluded.page_number,
                    page_text=excluded.page_text,
                    revision=excluded.revision,
                    updated_at=excluded.updated_at
                """,
                (session_id, paper_id, page_number, page_text, revision, updated_at),
            )
            connection.execute("DELETE FROM selections WHERE session_id = ?", (session_id,))
            if selection is not None:
                connection.execute(
                    """
                    INSERT INTO selections(
                        session_id, paper_id, page_number, exact_text, prefix, suffix,
                        normalized_quads_json, page_width, page_height, rotation, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        paper_id,
                        page_number,
                        selection["exact_text"],
                        selection["prefix"],
                        selection["suffix"],
                        json.dumps(selection["normalized_quads"], separators=(",", ":")),
                        selection["page_width"],
                        selection["page_height"],
                        selection["rotation"],
                        revision,
                    ),
                )
            connection.execute(
                "UPDATE reading_sessions SET updated_at = ? WHERE session_id = ?",
                (updated_at, session_id),
            )
            response = {
                "saved": True,
                "replayed": False,
                "paper_id": paper_id,
                "session_id": session_id,
                "page_number": page_number,
                "revision": revision,
                "updated_at": updated_at,
            }
            connection.execute(
                "INSERT INTO client_events(client_event_id, payload_sha256, response_json, created_at) VALUES (?, ?, ?, ?)",
                (
                    client_event_id,
                    payload_sha256,
                    json.dumps(response, separators=(",", ":")),
                    updated_at,
                ),
            )
            connection.commit()
            return response, False

    def get_context(self, session_id: str) -> ReadingContext:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT p.*, rs.session_id, rs.page_number, rs.page_text,
                       rs.revision, rs.updated_at
                FROM reading_states rs
                JOIN papers p ON p.paper_id = rs.paper_id
                WHERE rs.session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError("reading state not found")
            selection_row = connection.execute(
                "SELECT * FROM selections WHERE session_id = ?", (session_id,)
            ).fetchone()
        paper = self._paper(row)
        checkpoint = Checkpoint(
            paper_id=paper.paper_id,
            session_id=row["session_id"],
            page_number=row["page_number"],
            revision=row["revision"],
            updated_at=row["updated_at"],
        )
        selection = None
        if selection_row:
            selection = Selection(
                paper_id=selection_row["paper_id"],
                session_id=selection_row["session_id"],
                page_number=selection_row["page_number"],
                exact_text=selection_row["exact_text"],
                prefix=selection_row["prefix"],
                suffix=selection_row["suffix"],
                normalized_quads=json.loads(selection_row["normalized_quads_json"]),
                page_width=selection_row["page_width"],
                page_height=selection_row["page_height"],
                rotation=selection_row["rotation"],
                revision=selection_row["revision"],
            )
        return ReadingContext(
            paper=paper,
            session_id=row["session_id"],
            page_number=row["page_number"],
            page_text=row["page_text"],
            checkpoint=checkpoint,
            selection=selection,
        )

    def get_current_context(self) -> ReadingContext | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT rs.session_id
                FROM reading_states rs
                JOIN reading_sessions s ON s.session_id = rs.session_id
                ORDER BY s.updated_at DESC, rs.session_id ASC
                LIMIT 1
                """
            ).fetchone()
        return self.get_context(row["session_id"]) if row else None

    @staticmethod
    def _annotation(row: sqlite3.Row) -> Annotation:
        return Annotation(annotation_id=row["annotation_id"], thread_id=row["thread_id"], paper_id=row["paper_id"], session_id=row["session_id"], page_number=row["page_number"], exact_text=row["exact_text"], prefix=row["prefix"], suffix=row["suffix"], normalized_quads=json.loads(row["normalized_quads_json"]), page_width=row["page_width"], page_height=row["page_height"], rotation=row["rotation"], author=row["author"], note=row["note"], remember=bool(row["remember"]), style_key=row["style_key"], mark_type=row["mark_type"] if row["mark_type"] in {"underline", "highlight"} else "underline", created_at=row["created_at"], updated_at=row["updated_at"])

    def list_annotations(self, paper_id: str, page_number: int | None = None) -> list[Annotation]:
        with self.connection() as connection:
            if page_number is None:
                rows = connection.execute("SELECT * FROM annotations WHERE paper_id=? ORDER BY created_at, annotation_id", (paper_id,)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM annotations WHERE paper_id=? AND page_number=? ORDER BY created_at, annotation_id", (paper_id, page_number)).fetchall()
        return [self._annotation(row) for row in rows]

    def get_annotation(self, annotation_id: str) -> Annotation | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM annotations WHERE annotation_id=?", (annotation_id,)).fetchone()
        return self._annotation(row) if row else None

    def revoke_assistant_remember(self, annotation_id: str, updated_at: str) -> Annotation:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE annotations SET remember=0,updated_at=? WHERE annotation_id=? AND author='assistant' AND remember=1",
                (updated_at, annotation_id),
            )
            if changed.rowcount != 1:
                connection.rollback()
                raise RepositoryError("annotation is not an assistant remember")
            connection.commit()
        annotation = self.get_annotation(annotation_id)
        if annotation is None:
            raise NotFoundError("annotation was not found after remember revocation")
        return annotation

    def save_annotation(self, *, annotation: dict[str, Any], idempotency_key: str, payload_sha256: str, response_json: str, created_at: str, author: str = "assistant") -> tuple[dict[str, Any], bool]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT payload_sha256,response_json FROM annotation_events WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if existing:
                if existing["payload_sha256"] != payload_sha256: raise IdempotencyConflictError("idempotency_key was already used with a different payload")
                connection.rollback(); return json.loads(existing["response_json"]), True
            connection.execute("INSERT INTO annotations(annotation_id,thread_id,paper_id,session_id,page_number,exact_text,prefix,suffix,normalized_quads_json,page_width,page_height,rotation,author,note,remember,style_key,mark_type,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (annotation["annotation_id"], annotation["thread_id"], annotation["paper_id"], annotation["session_id"], annotation["page_number"], annotation["exact_text"], annotation["prefix"], annotation["suffix"], json.dumps(annotation["normalized_quads"], separators=(",",":")), annotation["page_width"], annotation["page_height"], annotation["rotation"], author, annotation.get("note"), int(annotation.get("remember", False)), annotation.get("style_key") if author == "user" else None, annotation.get("mark_type", "underline") if author == "user" else "underline", created_at, created_at))
            connection.execute("INSERT INTO annotation_events(idempotency_key,payload_sha256,response_json,created_at) VALUES(?,?,?,?)", (idempotency_key,payload_sha256,response_json,created_at)); connection.commit(); return json.loads(response_json), False

    def delete_user_annotation(self, annotation_id: str, session_id: str) -> bool:
        with self.connection() as connection:
            cur = connection.execute("DELETE FROM annotations WHERE annotation_id=? AND session_id=? AND author='user'", (annotation_id, session_id)); connection.commit(); return cur.rowcount == 1

    def update_user_annotation(self, annotation_id: str, session_id: str, *, style_key: str | None, mark_type: str, note: str | None, updated_at: str) -> Annotation:
        with self.connection() as connection:
            changed = connection.execute(
                "UPDATE annotations SET style_key=?,mark_type=?,note=?,updated_at=? WHERE annotation_id=? AND session_id=? AND author='user'",
                (style_key, mark_type, note, updated_at, annotation_id, session_id),
            )
            if changed.rowcount != 1:
                raise NotFoundError("user annotation was not found")
            connection.commit()
        result = self.get_annotation(annotation_id)
        if result is None:
            raise NotFoundError("user annotation was not found after update")
        return result

    @staticmethod
    def _sticky_note(row: sqlite3.Row) -> StickyNote:
        return StickyNote(id=row["id"], paper_id=row["paper_id"], page=row["page"], x=row["x"], y=row["y"], text=row["text"], style_key=row["style_key"], created_at=row["created_at"], updated_at=row["updated_at"])

    def list_sticky_notes(self, paper_id: str, page: int | None = None) -> list[StickyNote]:
        with self.connection() as connection:
            if page is None:
                rows = connection.execute("SELECT * FROM sticky_notes WHERE paper_id=? ORDER BY created_at, id", (paper_id,)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM sticky_notes WHERE paper_id=? AND page=? ORDER BY created_at, id", (paper_id, page)).fetchall()
        return [self._sticky_note(row) for row in rows]

    def get_sticky_note(self, note_id: str) -> StickyNote | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM sticky_notes WHERE id=?", (note_id,)).fetchone()
        return self._sticky_note(row) if row else None

    def insert_sticky_note(self, note: dict[str, Any], created_at: str) -> StickyNote:
        with self.connection() as connection:
            connection.execute("INSERT INTO sticky_notes(id,paper_id,page,x,y,text,style_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (note["id"], note["paper_id"], note["page"], note["x"], note["y"], note["text"], note["style_key"], created_at, created_at))
            connection.commit()
        result = self.get_sticky_note(note["id"])
        if result is None:
            raise NotFoundError("sticky note was not found after creation")
        return result

    def update_sticky_note(self, note_id: str, *, x: float, y: float, text: str, style_key: str, updated_at: str) -> StickyNote:
        with self.connection() as connection:
            changed = connection.execute("UPDATE sticky_notes SET x=?,y=?,text=?,style_key=?,updated_at=? WHERE id=?", (x, y, text, style_key, updated_at, note_id))
            if changed.rowcount != 1:
                raise NotFoundError("sticky note was not found")
            connection.commit()
        result = self.get_sticky_note(note_id)
        if result is None:
            raise NotFoundError("sticky note was not found after update")
        return result

    def delete_sticky_note(self, note_id: str) -> bool:
        with self.connection() as connection:
            changed = connection.execute("DELETE FROM sticky_notes WHERE id=?", (note_id,))
            connection.commit()
            return changed.rowcount == 1

    @staticmethod
    def _summary_note(row: sqlite3.Row) -> SummaryNote:
        return SummaryNote(
            summary_note_id=row["summary_note_id"],
            paper_id=row["paper_id"],
            page_number=row["page_number"],
            normalized_y=row["normalized_y"],
            text=row["text"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_summary_notes(self, paper_id: str, page_number: int | None = None) -> list[SummaryNote]:
        query = "SELECT * FROM summary_notes WHERE paper_id=?"
        values: list[object] = [paper_id]
        if page_number is not None:
            query += " AND page_number=?"
            values.append(page_number)
        query += " ORDER BY page_number, normalized_y, created_at, summary_note_id"
        with self.connection() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._summary_note(row) for row in rows]

    def get_summary_note(self, summary_note_id: str) -> SummaryNote | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM summary_notes WHERE summary_note_id=?", (summary_note_id,)
            ).fetchone()
        return self._summary_note(row) if row else None

    def insert_summary_note(self, note: dict[str, Any], created_at: str) -> SummaryNote:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO summary_notes(summary_note_id,paper_id,page_number,normalized_y,text,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (
                    note["summary_note_id"],
                    note["paper_id"],
                    note["page_number"],
                    note["normalized_y"],
                    note["text"],
                    created_at,
                    created_at,
                ),
            )
            connection.commit()
        result = self.get_summary_note(note["summary_note_id"])
        if result is None:
            raise NotFoundError("summary note was not found after creation")
        return result

    def update_summary_note(self, summary_note_id: str, *, text: str, updated_at: str) -> SummaryNote:
        with self.connection() as connection:
            changed = connection.execute(
                "UPDATE summary_notes SET text=?,updated_at=? WHERE summary_note_id=?",
                (text, updated_at, summary_note_id),
            )
            if changed.rowcount != 1:
                raise NotFoundError("summary note was not found")
            connection.commit()
        result = self.get_summary_note(summary_note_id)
        if result is None:
            raise NotFoundError("summary note was not found after update")
        return result

    def delete_summary_note(self, summary_note_id: str) -> bool:
        with self.connection() as connection:
            changed = connection.execute(
                "DELETE FROM summary_notes WHERE summary_note_id=?", (summary_note_id,)
            )
            connection.commit()
            return changed.rowcount == 1

    @staticmethod
    def _vocabulary(row: sqlite3.Row) -> VocabularyEntry:
        return VocabularyEntry(
            vocabulary_id=row["vocabulary_id"], term=row["term"], normalized_term=row["normalized_term"],
            definition=row["definition"], part_of_speech=row["part_of_speech"], pronunciation=row["pronunciation"],
            examples=json.loads(row["examples_json"]), notes=row["notes"], tags=json.loads(row["tags_json"]),
            source_paper_id=row["source_paper_id"], source_session_id=row["source_session_id"],
            source_page_number=row["source_page_number"],
            source_selection=json.loads(row["source_selection_json"]) if row["source_selection_json"] else None,
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def list_vocabulary(self, *, search: str | None = None, paper_id: str | None = None) -> list[VocabularyEntry]:
        query = "SELECT * FROM vocabulary_entries WHERE 1=1"
        values: list[str] = []
        if search:
            query += " AND (term LIKE ? OR normalized_term LIKE ? OR definition LIKE ?)"
            needle = f"%{search}%"
            values.extend([needle, needle, needle])
        if paper_id:
            query += " AND source_paper_id = ?"
            values.append(paper_id)
        query += " ORDER BY normalized_term COLLATE NOCASE, created_at, vocabulary_id"
        with self.connection() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._vocabulary(row) for row in rows]

    def get_vocabulary(self, vocabulary_id: str) -> VocabularyEntry | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM vocabulary_entries WHERE vocabulary_id=?", (vocabulary_id,)).fetchone()
        return self._vocabulary(row) if row else None

    def save_vocabulary(self, *, entry: dict[str, Any], idempotency_key: str, payload_sha256: str, response_json: str, created_at: str) -> tuple[dict[str, Any], bool]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT payload_sha256,response_json FROM vocabulary_events WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if existing:
                if existing["payload_sha256"] != payload_sha256:
                    raise IdempotencyConflictError("idempotency_key was already used with a different payload")
                connection.rollback()
                return json.loads(existing["response_json"]), True
            connection.execute(
                "INSERT INTO vocabulary_entries(vocabulary_id,term,normalized_term,definition,part_of_speech,pronunciation,examples_json,notes,tags_json,source_paper_id,source_session_id,source_page_number,source_selection_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (entry["vocabulary_id"], entry["term"], entry["normalized_term"], entry["definition"], entry["part_of_speech"], entry["pronunciation"], json.dumps(entry["examples"], ensure_ascii=False), entry["notes"], json.dumps(entry["tags"], ensure_ascii=False), entry["source_paper_id"], entry["source_session_id"], entry["source_page_number"], json.dumps(entry["source_selection"], ensure_ascii=False) if entry["source_selection"] else None, created_at, created_at),
            )
            response = json.loads(response_json)
            connection.execute("INSERT INTO vocabulary_events(idempotency_key,payload_sha256,response_json,created_at) VALUES (?,?,?,?)", (idempotency_key, payload_sha256, response_json, created_at))
            connection.commit()
            return response, False

    def update_vocabulary(self, vocabulary_id: str, values: dict[str, Any], updated_at: str) -> VocabularyEntry:
        with self.connection() as connection:
            changed = connection.execute(
                "UPDATE vocabulary_entries SET term=?,normalized_term=?,definition=?,part_of_speech=?,pronunciation=?,examples_json=?,notes=?,tags_json=?,updated_at=? WHERE vocabulary_id=?",
                (values["term"], values["normalized_term"], values["definition"], values["part_of_speech"], values["pronunciation"], json.dumps(values["examples"], ensure_ascii=False), values["notes"], json.dumps(values["tags"], ensure_ascii=False), updated_at, vocabulary_id),
            )
            if changed.rowcount != 1:
                raise NotFoundError("vocabulary entry not found")
            connection.commit()
        result = self.get_vocabulary(vocabulary_id)
        if result is None:
            raise NotFoundError("vocabulary entry not found after update")
        return result

    def delete_vocabulary(self, vocabulary_id: str) -> bool:
        with self.connection() as connection:
            changed = connection.execute("DELETE FROM vocabulary_entries WHERE vocabulary_id=?", (vocabulary_id,))
            connection.commit()
            return changed.rowcount == 1

    def add_vocabulary_review(self, *, review_id: str, vocabulary_id: str, review_session_id: str, rating: str, mode: str, reviewed_at: str) -> VocabularyReviewResult:
        with self.connection() as connection:
            if connection.execute("SELECT 1 FROM vocabulary_entries WHERE vocabulary_id=?", (vocabulary_id,)).fetchone() is None:
                raise NotFoundError("vocabulary entry not found")
            connection.execute("INSERT INTO vocabulary_review_results(review_id,vocabulary_id,review_session_id,rating,mode,reviewed_at) VALUES (?,?,?,?,?,?)", (review_id, vocabulary_id, review_session_id, rating, mode, reviewed_at))
            connection.commit()
        return VocabularyReviewResult(review_id, vocabulary_id, review_session_id, rating, mode, reviewed_at)

    @staticmethod
    def _reading_task(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "task_id": row["task_id"],
            "paper_id": row["paper_id"],
            "title": row["title"],
            "instructions": row["instructions"],
            "status": row["status"],
            "origin_kind": row["origin_kind"],
            "origin_ref": row["origin_ref"],
            "due_at": row["due_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _reading_question(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "question_id": row["question_id"],
            "task_id": row["task_id"],
            "ordinal": row["ordinal"],
            "question_type": row["question_type"],
            "prompt": row["prompt"],
            "choices": json.loads(row["choices_json"]) if row["choices_json"] else None,
            "reference_answer": row["reference_answer"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _reading_feedback(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "feedback_id": row["feedback_id"],
            "submission_id": row["submission_id"],
            "revision": row["revision"],
            "feedback_text": row["feedback_text"],
            "question_feedback": json.loads(row["question_feedback_json"]) if row["question_feedback_json"] else None,
            "source_kind": row["source_kind"],
            "created_at": row["created_at"],
        }

    @classmethod
    def _reading_submission(cls, connection: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT * FROM reading_submissions WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        answers = connection.execute(
            """
            SELECT a.submission_id, a.question_id, a.answer_text, a.selected_choice,
                   a.created_at, a.updated_at
            FROM reading_answers a
            JOIN reading_questions q ON q.question_id = a.question_id
            WHERE a.submission_id = ?
            ORDER BY q.ordinal, q.question_id
            """,
            (row["submission_id"],),
        ).fetchall()
        return {
            "submission_id": row["submission_id"],
            "task_id": row["task_id"],
            "status": row["status"],
            "revision": row["revision"],
            "submitted_at": row["submitted_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "answers": [
                {
                    "question_id": answer["question_id"],
                    "answer_text": answer["answer_text"],
                    "selected_choice": answer["selected_choice"],
                    "created_at": answer["created_at"],
                    "updated_at": answer["updated_at"],
                }
                for answer in answers
            ],
        }

    @staticmethod
    def _event_result(connection: sqlite3.Connection, event_id: str, payload_sha256: str) -> dict[str, Any] | None:
        event = connection.execute(
            "SELECT payload_sha256, response_json FROM client_events WHERE client_event_id = ?",
            (event_id,),
        ).fetchone()
        if event is None:
            return None
        if event["payload_sha256"] != payload_sha256:
            raise IdempotencyConflictError("client_event_id was already used with a different payload")
        return json.loads(event["response_json"])

    def save_reading_task(
        self,
        *,
        task: dict[str, Any],
        questions: list[dict[str, Any]],
        idempotency_key: str,
        payload_sha256: str,
        response_json: str,
    ) -> tuple[dict[str, Any], bool]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._event_result(connection, idempotency_key, payload_sha256)
            if replay is not None:
                connection.rollback()
                return replay, True
            if connection.execute("SELECT 1 FROM papers WHERE paper_id = ?", (task["paper_id"],)).fetchone() is None:
                raise NotFoundError("paper not found")
            connection.execute(
                """
                INSERT INTO reading_tasks(
                    task_id, paper_id, title, instructions, status, origin_kind,
                    origin_ref, due_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (task["task_id"], task["paper_id"], task["title"], task["instructions"], task["status"], task["origin_kind"], task["origin_ref"], task["due_at"], task["created_at"], task["updated_at"]),
            )
            for question in questions:
                connection.execute(
                    """
                    INSERT INTO reading_questions(
                        question_id, task_id, ordinal, question_type, prompt,
                        choices_json, reference_answer, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (question["question_id"], task["task_id"], question["ordinal"], question["question_type"], question["prompt"], json.dumps(question["choices"], ensure_ascii=False, separators=(",", ":")) if question["choices"] else None, question["reference_answer"], question["created_at"], question["updated_at"]),
                )
            connection.execute(
                "INSERT INTO client_events(client_event_id, payload_sha256, response_json, created_at) VALUES (?, ?, ?, ?)",
                (idempotency_key, payload_sha256, response_json, task["created_at"]),
            )
            connection.commit()
            return json.loads(response_json), False

    def get_reading_task(self, task_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM reading_tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise NotFoundError("reading task not found")
        return self._reading_task(row)

    def list_reading_tasks(self, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM reading_tasks"
        values: list[str] = []
        if status:
            query += " WHERE status = ?"
            values.append(status)
        query += " ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'draft' THEN 1 ELSE 2 END, due_at IS NULL, due_at, updated_at DESC, task_id"
        with self.connection() as connection:
            rows = connection.execute(query, values).fetchall()
            results: list[dict[str, Any]] = []
            for row in rows:
                task = self._reading_task(row)
                paper = connection.execute("SELECT * FROM papers WHERE paper_id = ?", (row["paper_id"],)).fetchone()
                submission = connection.execute("SELECT status FROM reading_submissions WHERE task_id = ?", (row["task_id"],)).fetchone()
                question_count = connection.execute("SELECT count(*) AS count FROM reading_questions WHERE task_id = ?", (row["task_id"],)).fetchone()["count"]
                task["paper"] = self._paper(paper).public_dict() if paper else None
                task["submission_status"] = submission["status"] if submission else None
                task["questions_count"] = question_count
                results.append(task)
        return results

    def get_reading_questions(self, task_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM reading_questions WHERE task_id = ? ORDER BY ordinal, question_id", (task_id,)).fetchall()
        return [self._reading_question(row) for row in rows]

    def open_reading_task(self, task_id: str, session_id: str, now: str) -> str:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute("SELECT paper_id FROM reading_tasks WHERE task_id = ?", (task_id,)).fetchone()
            if task is None:
                raise NotFoundError("reading task not found")
            existing = connection.execute("SELECT session_id FROM reading_task_sessions WHERE task_id = ?", (task_id,)).fetchone()
            if existing is not None:
                connection.rollback()
                return existing["session_id"]
            connection.execute(
                "INSERT INTO reading_sessions(session_id, paper_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, task["paper_id"], now, now),
            )
            connection.execute(
                "INSERT INTO reading_task_sessions(task_id, session_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (task_id, session_id, now, now),
            )
            connection.commit()
            return session_id

    def get_reading_task_session(self, task_id: str) -> str | None:
        with self.connection() as connection:
            row = connection.execute("SELECT session_id FROM reading_task_sessions WHERE task_id = ?", (task_id,)).fetchone()
        return row["session_id"] if row else None

    def get_reading_submission(self, task_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            return self._reading_submission(connection, task_id)

    def save_reading_draft(
        self,
        *,
        task_id: str,
        submission_id: str,
        expected_revision: int,
        answers: list[dict[str, Any]],
        client_event_id: str,
        payload_sha256: str,
        now: str,
    ) -> tuple[dict[str, Any], bool]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._event_result(connection, client_event_id, payload_sha256)
            if replay is not None:
                connection.rollback()
                return replay, True
            task = connection.execute("SELECT status FROM reading_tasks WHERE task_id = ?", (task_id,)).fetchone()
            if task is None:
                raise NotFoundError("reading task not found")
            if task["status"] == "archived":
                raise RepositoryError("archived reading task is read-only")
            current = connection.execute("SELECT * FROM reading_submissions WHERE task_id = ?", (task_id,)).fetchone()
            if current is not None:
                if current["status"] != "draft":
                    raise RepositoryError("submitted reading submission is read-only")
                if current["revision"] != expected_revision:
                    raise RevisionConflictError(current["revision"], expected_revision)
                submission_id = current["submission_id"]
                next_revision = current["revision"] + 1
                connection.execute("UPDATE reading_submissions SET revision=?, updated_at=? WHERE submission_id=?", (next_revision, now, submission_id))
            else:
                if expected_revision != 0:
                    raise RevisionConflictError(0, expected_revision)
                next_revision = 1
                connection.execute(
                    "INSERT INTO reading_submissions(submission_id, task_id, status, revision, submitted_at, created_at, updated_at) VALUES (?, ?, 'draft', ?, NULL, ?, ?)",
                    (submission_id, task_id, next_revision, now, now),
                )
            for answer in answers:
                question = connection.execute("SELECT question_type FROM reading_questions WHERE question_id = ? AND task_id = ?", (answer["question_id"], task_id)).fetchone()
                if question is None:
                    raise RepositoryError("answer question does not belong to reading task")
                connection.execute(
                    """
                    INSERT INTO reading_answers(submission_id, question_id, answer_text, selected_choice, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(submission_id, question_id) DO UPDATE SET
                        answer_text=excluded.answer_text,
                        selected_choice=excluded.selected_choice,
                        updated_at=excluded.updated_at
                    """,
                    (submission_id, answer["question_id"], answer["answer_text"], answer["selected_choice"], now, now),
                )
            response = {"submission": self._reading_submission(connection, task_id), "replayed": False}
            connection.execute(
                "INSERT INTO client_events(client_event_id, payload_sha256, response_json, created_at) VALUES (?, ?, ?, ?)",
                (client_event_id, payload_sha256, json.dumps(response, ensure_ascii=False, separators=(",", ":")), now),
            )
            connection.commit()
            return response, False

    def submit_reading(
        self,
        *,
        task_id: str,
        expected_revision: int,
        client_event_id: str,
        payload_sha256: str,
        now: str,
    ) -> tuple[dict[str, Any], bool]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._event_result(connection, client_event_id, payload_sha256)
            if replay is not None:
                connection.rollback()
                return replay, True
            task = connection.execute("SELECT status FROM reading_tasks WHERE task_id = ?", (task_id,)).fetchone()
            if task is None:
                raise NotFoundError("reading task not found")
            submission = connection.execute("SELECT * FROM reading_submissions WHERE task_id = ?", (task_id,)).fetchone()
            if submission is None:
                raise RepositoryError("reading task has no draft submission")
            if submission["status"] != "draft":
                raise RepositoryError("reading submission is already submitted")
            if submission["revision"] != expected_revision:
                raise RevisionConflictError(submission["revision"], expected_revision)
            questions = connection.execute("SELECT question_id, question_type FROM reading_questions WHERE task_id = ? ORDER BY ordinal", (task_id,)).fetchall()
            answers = {row["question_id"]: row for row in connection.execute("SELECT * FROM reading_answers WHERE submission_id = ?", (submission["submission_id"],)).fetchall()}
            for question in questions:
                answer = answers.get(question["question_id"])
                if answer is None:
                    raise RepositoryError("all reading questions require an answer")
                if question["question_type"] == "short_text" and not (answer["answer_text"] or "").strip():
                    raise RepositoryError("all reading questions require an answer")
                if question["question_type"] == "single_choice" and not answer["selected_choice"]:
                    raise RepositoryError("all reading questions require an answer")
            next_revision = submission["revision"] + 1
            connection.execute("UPDATE reading_submissions SET status='submitted', revision=?, submitted_at=?, updated_at=? WHERE submission_id=?", (next_revision, now, now, submission["submission_id"]))
            response = {"submission": self._reading_submission(connection, task_id), "replayed": False}
            connection.execute(
                "INSERT INTO client_events(client_event_id, payload_sha256, response_json, created_at) VALUES (?, ?, ?, ?)",
                (client_event_id, payload_sha256, json.dumps(response, ensure_ascii=False, separators=(",", ":")), now),
            )
            connection.commit()
            return response, False

    def list_reading_feedback(self, task_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT f.* FROM reading_feedback f
                JOIN reading_submissions s ON s.submission_id = f.submission_id
                WHERE s.task_id = ? ORDER BY f.revision
                """,
                (task_id,),
            ).fetchall()
        return [self._reading_feedback(row) for row in rows]

    def add_reading_feedback(
        self,
        *,
        task_id: str,
        feedback_id: str,
        feedback_text: str,
        question_feedback_json: str | None,
        source_kind: str,
        idempotency_key: str | None,
        payload_sha256: str | None,
        now: str,
        expected_submission_id: str | None = None,
        expected_submission_revision: int | None = None,
        reject_archived_task: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key and payload_sha256:
                replay = self._event_result(connection, idempotency_key, payload_sha256)
                if replay is not None:
                    connection.rollback()
                    return replay, True
            task = connection.execute(
                "SELECT status FROM reading_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise NotFoundError("reading task not found")
            if reject_archived_task and task["status"] == "archived":
                raise RepositoryError("archived reading task is read-only")
            submission = connection.execute("SELECT * FROM reading_submissions WHERE task_id = ?", (task_id,)).fetchone()
            if submission is None:
                raise NotFoundError("reading submission not found")
            if expected_submission_id is not None and submission["submission_id"] != expected_submission_id:
                raise RepositoryError("reading submission identity mismatch")
            if expected_submission_revision is not None and submission["revision"] != expected_submission_revision:
                raise RevisionConflictError(submission["revision"], expected_submission_revision)
            if submission["status"] not in {"submitted", "reviewed"}:
                raise RepositoryError("feedback requires a submitted reading submission")
            next_revision = connection.execute("SELECT COALESCE(MAX(revision), 0) + 1 AS next_revision FROM reading_feedback WHERE submission_id = ?", (submission["submission_id"],)).fetchone()["next_revision"]
            connection.execute(
                "INSERT INTO reading_feedback(feedback_id, submission_id, revision, feedback_text, question_feedback_json, source_kind, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (feedback_id, submission["submission_id"], next_revision, feedback_text, question_feedback_json, source_kind, now),
            )
            connection.execute("UPDATE reading_submissions SET status='reviewed', updated_at=? WHERE submission_id=?", (now, submission["submission_id"]))
            row = connection.execute("SELECT * FROM reading_feedback WHERE feedback_id = ?", (feedback_id,)).fetchone()
            response = {"feedback": self._reading_feedback(row), "replayed": False}
            if idempotency_key and payload_sha256:
                connection.execute(
                    "INSERT INTO client_events(client_event_id, payload_sha256, response_json, created_at) VALUES (?, ?, ?, ?)",
                    (idempotency_key, payload_sha256, json.dumps(response, ensure_ascii=False, separators=(",", ":")), now),
                )
            connection.commit()
            return response, False

    def archive_reading_task(self, task_id: str, now: str) -> dict[str, Any]:
        with self.connection() as connection:
            changed = connection.execute("UPDATE reading_tasks SET status='archived', updated_at=? WHERE task_id=? AND status != 'archived'", (now, task_id))
            if changed.rowcount == 0 and connection.execute("SELECT 1 FROM reading_tasks WHERE task_id=?", (task_id,)).fetchone() is None:
                raise NotFoundError("reading task not found")
            connection.commit()
        return self.get_reading_task(task_id)
