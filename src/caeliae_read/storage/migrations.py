from __future__ import annotations

import json
import sqlite3
from uuid import NAMESPACE_URL, uuid5


MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE papers (
            paper_id TEXT PRIMARY KEY,
            content_sha256 TEXT NOT NULL UNIQUE,
            original_filename TEXT NOT NULL,
            storage_key TEXT NOT NULL UNIQUE,
            size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
            mime_type TEXT NOT NULL CHECK (mime_type = 'application/pdf'),
            page_count INTEGER NOT NULL CHECK (page_count > 0),
            created_at TEXT NOT NULL
        );

        CREATE TABLE reading_sessions (
            session_id TEXT PRIMARY KEY,
            paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE reading_states (
            session_id TEXT PRIMARY KEY REFERENCES reading_sessions(session_id) ON DELETE CASCADE,
            paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
            page_number INTEGER NOT NULL CHECK (page_number >= 1),
            page_text TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK (revision >= 1),
            updated_at TEXT NOT NULL
        );

        CREATE TABLE selections (
            session_id TEXT PRIMARY KEY REFERENCES reading_sessions(session_id) ON DELETE CASCADE,
            paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
            page_number INTEGER NOT NULL CHECK (page_number >= 1),
            exact_text TEXT NOT NULL,
            prefix TEXT NOT NULL,
            suffix TEXT NOT NULL,
            normalized_quads_json TEXT NOT NULL,
            page_width REAL NOT NULL CHECK (page_width > 0),
            page_height REAL NOT NULL CHECK (page_height > 0),
            rotation INTEGER NOT NULL,
            revision INTEGER NOT NULL CHECK (revision >= 1)
        );

        CREATE TABLE client_events (
            client_event_id TEXT PRIMARY KEY,
            payload_sha256 TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX idx_reading_sessions_updated
            ON reading_sessions(updated_at DESC);
        """,
    ),
    (
        2,
        """
        CREATE TABLE annotations (
            annotation_id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
            session_id TEXT NOT NULL REFERENCES reading_sessions(session_id) ON DELETE CASCADE,
            page_number INTEGER NOT NULL,
            exact_text TEXT NOT NULL,
            prefix TEXT NOT NULL,
            suffix TEXT NOT NULL,
            normalized_quads_json TEXT NOT NULL,
            page_width REAL NOT NULL,
            page_height REAL NOT NULL,
            rotation INTEGER NOT NULL,
            author TEXT NOT NULL CHECK (author IN ('user','assistant')),
            note TEXT,
            remember INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(thread_id, annotation_id)
        );
        CREATE TABLE annotation_events (
            idempotency_key TEXT PRIMARY KEY,
            payload_sha256 TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_annotations_paper_page ON annotations(paper_id, page_number, created_at);
        """,
    ),
    (
        3,
        """
        ALTER TABLE annotations ADD COLUMN style_key TEXT
            CHECK (style_key IS NULL OR style_key IN ('primary','secondary','tertiary'));
        """,
    ),
    (
        4,
        """
        CREATE TABLE sticky_notes (
            id TEXT PRIMARY KEY,
            paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
            page INTEGER NOT NULL CHECK (page >= 1),
            x REAL NOT NULL CHECK (x >= 0 AND x <= 1),
            y REAL NOT NULL CHECK (y >= 0 AND y <= 1),
            text TEXT NOT NULL,
            style_key TEXT NOT NULL CHECK (style_key IN ('primary','secondary','tertiary')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX idx_sticky_notes_paper_page ON sticky_notes(paper_id, page, created_at);
        """,
    ),
    (
        5,
        """
        ALTER TABLE annotations ADD COLUMN mark_type TEXT NOT NULL DEFAULT 'underline'
            CHECK (mark_type IN ('underline','highlight'));
        """,
    ),
    (
        6,
        """
        CREATE TABLE vocabulary_entries (
            vocabulary_id TEXT PRIMARY KEY,
            term TEXT NOT NULL CHECK (length(term) BETWEEN 1 AND 200),
            normalized_term TEXT NOT NULL CHECK (length(normalized_term) BETWEEN 1 AND 200),
            definition TEXT,
            part_of_speech TEXT,
            pronunciation TEXT,
            examples_json TEXT NOT NULL DEFAULT '[]',
            notes TEXT,
            tags_json TEXT NOT NULL DEFAULT '[]',
            source_paper_id TEXT REFERENCES papers(paper_id) ON DELETE SET NULL,
            source_session_id TEXT REFERENCES reading_sessions(session_id) ON DELETE SET NULL,
            source_page_number INTEGER CHECK (source_page_number IS NULL OR source_page_number >= 1),
            source_selection_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX idx_vocabulary_normalized_term ON vocabulary_entries(normalized_term);
        CREATE INDEX idx_vocabulary_source_paper ON vocabulary_entries(source_paper_id, source_page_number);
        CREATE TABLE vocabulary_events (
            idempotency_key TEXT PRIMARY KEY,
            payload_sha256 TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """,
    ),
    (
        7,
        """
        CREATE TABLE vocabulary_review_results (
            review_id TEXT PRIMARY KEY,
            vocabulary_id TEXT NOT NULL REFERENCES vocabulary_entries(vocabulary_id) ON DELETE CASCADE,
            review_session_id TEXT NOT NULL,
            rating TEXT NOT NULL CHECK (rating IN ('unknown','fuzzy','known')),
            mode TEXT NOT NULL DEFAULT 'local' CHECK (mode IN ('local','agent')),
            reviewed_at TEXT NOT NULL
        );
        CREATE INDEX idx_vocabulary_reviews_session ON vocabulary_review_results(review_session_id, reviewed_at);
        """,
    ),
    (
        8,
        """
        CREATE TABLE reading_tasks (
            task_id TEXT PRIMARY KEY,
            paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE RESTRICT,
            title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
            instructions TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK (status IN ('draft','active','archived')),
            origin_kind TEXT NOT NULL CHECK (origin_kind IN ('manual','agent','daily')),
            origin_ref TEXT,
            due_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX idx_reading_tasks_status ON reading_tasks(status, updated_at DESC);

        CREATE TABLE reading_questions (
            question_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES reading_tasks(task_id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
            question_type TEXT NOT NULL CHECK (question_type IN ('short_text','single_choice')),
            prompt TEXT NOT NULL CHECK (length(prompt) BETWEEN 1 AND 2_000),
            choices_json TEXT,
            reference_answer TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(task_id, ordinal)
        );
        CREATE INDEX idx_reading_questions_task ON reading_questions(task_id, ordinal);

        CREATE TABLE reading_task_sessions (
            task_id TEXT PRIMARY KEY REFERENCES reading_tasks(task_id) ON DELETE CASCADE,
            session_id TEXT NOT NULL UNIQUE REFERENCES reading_sessions(session_id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE reading_submissions (
            submission_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL UNIQUE REFERENCES reading_tasks(task_id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK (status IN ('draft','submitted','reviewed')),
            revision INTEGER NOT NULL CHECK (revision >= 1),
            submitted_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE reading_answers (
            submission_id TEXT NOT NULL REFERENCES reading_submissions(submission_id) ON DELETE CASCADE,
            question_id TEXT NOT NULL REFERENCES reading_questions(question_id) ON DELETE CASCADE,
            answer_text TEXT,
            selected_choice TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(submission_id, question_id)
        );

        CREATE TABLE reading_feedback (
            feedback_id TEXT PRIMARY KEY,
            submission_id TEXT NOT NULL REFERENCES reading_submissions(submission_id) ON DELETE CASCADE,
            revision INTEGER NOT NULL CHECK (revision >= 1),
            feedback_text TEXT NOT NULL CHECK (length(feedback_text) BETWEEN 1 AND 4_000),
            question_feedback_json TEXT,
            source_kind TEXT NOT NULL CHECK (source_kind IN ('manual','agent')),
            created_at TEXT NOT NULL,
            UNIQUE(submission_id, revision)
        );
        CREATE INDEX idx_reading_feedback_submission ON reading_feedback(submission_id, revision DESC);
        """,
    ),
    (
        9,
        """
        CREATE TABLE summary_notes (
            summary_note_id TEXT PRIMARY KEY,
            paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
            page_number INTEGER NOT NULL CHECK (page_number >= 1),
            normalized_y REAL NOT NULL CHECK (normalized_y >= 0 AND normalized_y <= 1),
            text TEXT NOT NULL CHECK (length(trim(text)) BETWEEN 1 AND 2_000),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX idx_summary_notes_paper_position
            ON summary_notes(paper_id, page_number, normalized_y, created_at, summary_note_id);
        """,
    ),
    (
        10,
        """
        ALTER TABLE vocabulary_entries ADD COLUMN lemma TEXT
            CHECK (lemma IS NULL OR length(trim(lemma)) BETWEEN 1 AND 200);
        ALTER TABLE vocabulary_entries ADD COLUMN entry_type TEXT NOT NULL DEFAULT 'word'
            CHECK (entry_type IN ('word','phrase'));

        CREATE TABLE vocabulary_occurrences (
            occurrence_id TEXT PRIMARY KEY,
            vocabulary_id TEXT NOT NULL REFERENCES vocabulary_entries(vocabulary_id) ON DELETE CASCADE,
            surface_form TEXT NOT NULL CHECK (length(trim(surface_form)) >= 1),
            paper_id TEXT REFERENCES papers(paper_id) ON DELETE SET NULL,
            session_id TEXT REFERENCES reading_sessions(session_id) ON DELETE SET NULL,
            page_number INTEGER CHECK (page_number IS NULL OR page_number >= 1),
            context_excerpt TEXT,
            selection_json TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_vocabulary_occurrences_entry
            ON vocabulary_occurrences(vocabulary_id, created_at, occurrence_id);
        CREATE INDEX idx_vocabulary_occurrences_surface
            ON vocabulary_occurrences(surface_form COLLATE NOCASE);
        CREATE INDEX idx_vocabulary_occurrences_paper_page
            ON vocabulary_occurrences(paper_id, page_number, created_at);

        CREATE TABLE vocabulary_occurrence_events (
            idempotency_key TEXT PRIMARY KEY,
            payload_sha256 TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """,
    ),
)


def _entry_type_for_term(term: str) -> str:
    return "phrase" if any(character.isspace() for character in term.strip()) else "word"


def _safe_selection_metadata(raw_selection: str | None) -> tuple[str, str | None, str | None]:
    """Return surface/context while preserving raw selection JSON in storage."""
    if not raw_selection:
        return "", None, None
    try:
        selection = json.loads(raw_selection)
    except (TypeError, ValueError, json.JSONDecodeError):
        return "", None, raw_selection
    if not isinstance(selection, dict):
        return "", None, raw_selection
    exact_text = selection.get("exact_text")
    if not isinstance(exact_text, str) or not exact_text.strip():
        return "", None, raw_selection
    prefix = selection.get("prefix", "")
    suffix = selection.get("suffix", "")
    prefix = prefix if isinstance(prefix, str) else ""
    suffix = suffix if isinstance(suffix, str) else ""
    context = f"{prefix}{exact_text}{suffix}" or None
    return exact_text, context, raw_selection


def _backfill_vocabulary_occurrences(connection: sqlite3.Connection) -> None:
    """Add one initial occurrence for each legacy entry that has source data."""
    rows = connection.execute(
        """
        SELECT vocabulary_id, term, source_paper_id, source_session_id,
               source_page_number, source_selection_json
        FROM vocabulary_entries
        """
    ).fetchall()
    for row in rows:
        connection.execute(
            "UPDATE vocabulary_entries SET entry_type=? WHERE vocabulary_id=?",
            (_entry_type_for_term(row[1]), row[0]),
        )
        has_source = any(
            value is not None for value in row[2:6]
        )
        if not has_source:
            continue
        surface_form, context_excerpt, selection_json = _safe_selection_metadata(row[5])
        surface_form = surface_form or row[1]
        occurrence_id = str(
            uuid5(NAMESPACE_URL, f"caeliae-read:vocabulary-occurrence:{row[0]}")
        )
        connection.execute(
            """
            INSERT INTO vocabulary_occurrences(
                occurrence_id, vocabulary_id, surface_form, paper_id, session_id,
                page_number, context_excerpt, selection_json, created_at
            )
            SELECT ?, ?, ?, ?, ?, ?, ?, ?, created_at
            FROM vocabulary_entries
            WHERE vocabulary_id = ?
            """,
            (
                occurrence_id,
                row[0],
                surface_form,
                row[2],
                row[3],
                row[4],
                context_excerpt,
                selection_json,
                row[0],
            ),
        )


MIGRATION_HOOKS = {10: _backfill_vocabulary_occurrences}


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {
        row[0] for row in connection.execute("SELECT version FROM schema_migrations")
    }
    for version, sql in MIGRATIONS:
        if version in applied:
            continue
        try:
            connection.executescript(f"BEGIN;\n{sql}\n")
            hook = MIGRATION_HOOKS.get(version)
            if hook:
                hook(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                (version,),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
