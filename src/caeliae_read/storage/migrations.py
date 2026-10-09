from __future__ import annotations

import sqlite3


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
)


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
        connection.executescript(sql)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
            (version,),
        )
    connection.commit()
