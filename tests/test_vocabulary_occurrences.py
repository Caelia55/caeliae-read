from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from caeliae_read.storage.migrations import MIGRATIONS, migrate
from caeliae_read.storage.repository import CaeliaeReadRepository, IdempotencyConflictError


def _bootstrap(database: Path, through: int) -> sqlite3.Connection:
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    for version, sql in MIGRATIONS[:through]:
        connection.executescript(sql)
        connection.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, 'old')", (version,))
    connection.commit()
    return connection


def _insert_paper_and_session(connection: sqlite3.Connection, *, paper_id: str = "paper-1", session_id: str = "session-1") -> None:
    connection.execute(
        "INSERT INTO papers(paper_id, content_sha256, original_filename, storage_key, size_bytes, mime_type, page_count, created_at) VALUES (?, ?, ?, ?, ?, 'application/pdf', ?, ?)",
        (paper_id, f"hash-{paper_id}", "paper.pdf", f"{paper_id}.pdf", 1, 2, "old"),
    )
    connection.execute(
        "INSERT INTO reading_sessions(session_id, paper_id, created_at, updated_at) VALUES (?, ?, 'old', 'old')",
        (session_id, paper_id),
    )


def _insert_entry(
    connection: sqlite3.Connection,
    vocabulary_id: str,
    term: str,
    *,
    paper_id: str | None = None,
    session_id: str | None = None,
    page_number: int | None = None,
    selection_json: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO vocabulary_entries(
            vocabulary_id, term, normalized_term, definition, part_of_speech,
            pronunciation, examples_json, notes, tags_json, source_paper_id,
            source_session_id, source_page_number, source_selection_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, NULL, NULL, NULL, '[]', NULL, '[]', ?, ?, ?, ?, 'old', 'old')
        """,
        (
            vocabulary_id, term, term.casefold(), paper_id, session_id,
            page_number, selection_json,
        ),
    )


def _selection_json() -> str:
    return json.dumps(
        {
            "paper_id": "paper-1",
            "session_id": "session-1",
            "page_number": 2,
            "exact_text": "traces",
            "prefix": "interaction ",
            "suffix": " in the model",
            "normalized_quads": [{"x": 0.1, "y": 0.2, "width": 0.1, "height": 0.03}],
            "page_width": 612,
            "page_height": 792,
            "rotation": 0,
            "revision": 3,
        }
    )


def test_fresh_database_reaches_v12_without_changing_legacy_api_shape(tmp_path: Path):
    repository = CaeliaeReadRepository(tmp_path / "fresh.sqlite3")
    with repository.connection() as connection:
        assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 12
        columns = {row[1] for row in connection.execute("PRAGMA table_info(vocabulary_entries)")}
        assert {"lemma", "entry_type"}.issubset(columns)
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vocabulary_occurrences'").fetchone()
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vocabulary_occurrence_events'").fetchone()

    assert repository.list_vocabulary() == []


def test_v9_to_v12_backfills_sources_without_merging_or_changing_reviews(tmp_path: Path):
    database = tmp_path / "v9.sqlite3"
    connection = _bootstrap(database, 9)
    _insert_paper_and_session(connection)
    _insert_entry(
        connection,
        "vocab-sourced",
        "traces",
        paper_id="paper-1",
        session_id="session-1",
        page_number=2,
        selection_json=_selection_json(),
    )
    _insert_entry(connection, "vocab-phrase", "latent representation")
    _insert_entry(connection, "vocab-word", "trace")
    _insert_entry(
        connection,
        "vocab-malformed",
        "opaque",
        paper_id="paper-1",
        session_id="session-1",
        page_number=1,
        selection_json="{malformed",
    )
    connection.execute(
        "INSERT INTO vocabulary_review_results(review_id, vocabulary_id, review_session_id, rating, mode, reviewed_at) VALUES ('review-1', 'vocab-sourced', 'review-session', 'known', 'local', 'old')"
    )
    connection.commit()

    migrate(connection)

    assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 12
    assert connection.execute("SELECT count(*) FROM vocabulary_entries").fetchone()[0] == 4
    assert connection.execute("SELECT count(*) FROM vocabulary_review_results").fetchone()[0] == 1
    assert connection.execute("SELECT lemma FROM vocabulary_entries WHERE vocabulary_id='vocab-sourced'").fetchone()[0] is None
    mirror = connection.execute(
        "SELECT source_paper_id, source_session_id, source_page_number, source_selection_json FROM vocabulary_entries WHERE vocabulary_id='vocab-sourced'"
    ).fetchone()
    assert tuple(mirror) == ("paper-1", "session-1", 2, _selection_json())
    assert connection.execute("SELECT entry_type FROM vocabulary_entries WHERE vocabulary_id='vocab-phrase'").fetchone()[0] == "phrase"
    assert connection.execute("SELECT entry_type FROM vocabulary_entries WHERE vocabulary_id='vocab-word'").fetchone()[0] == "word"
    assert connection.execute("SELECT entry_type FROM vocabulary_entries WHERE vocabulary_id='vocab-malformed'").fetchone()[0] == "word"

    occurrence = connection.execute(
        "SELECT vocabulary_id, surface_form, paper_id, session_id, page_number, context_excerpt, selection_json FROM vocabulary_occurrences WHERE vocabulary_id='vocab-sourced'"
    ).fetchone()
    assert occurrence[0] == "vocab-sourced"
    assert occurrence[1] == "traces"
    assert occurrence[2:5] == ("paper-1", "session-1", 2)
    assert occurrence[5] == "interaction traces in the model"
    assert json.loads(occurrence[6]) == json.loads(_selection_json())

    malformed = connection.execute(
        "SELECT surface_form, context_excerpt, selection_json FROM vocabulary_occurrences WHERE vocabulary_id='vocab-malformed'"
    ).fetchone()
    assert tuple(malformed) == ("opaque", None, "{malformed")
    assert connection.execute("SELECT count(*) FROM vocabulary_occurrences WHERE vocabulary_id='vocab-phrase'").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM vocabulary_occurrences WHERE vocabulary_id='vocab-word'").fetchone()[0] == 0
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_occurrence_repository_and_idempotency_storage(tmp_path: Path):
    database = tmp_path / "repository.sqlite3"
    repository = CaeliaeReadRepository(database)
    with repository.connection() as connection:
        _insert_paper_and_session(connection)
        _insert_entry(connection, "vocab-1", "trace")
        connection.commit()

    occurrence = repository.create_vocabulary_occurrence(
        occurrence_id=str(uuid4()), vocabulary_id="vocab-1", surface_form="traces",
        paper_id="paper-1", session_id="session-1", page_number=2,
        context_excerpt="interaction traces", selection_json=_selection_json(), created_at="now",
    )
    assert repository.get_vocabulary_occurrence(occurrence.occurrence_id) == occurrence
    assert repository.list_vocabulary_occurrences("vocab-1") == [occurrence]

    response = json.dumps({"occurrence_id": occurrence.occurrence_id})
    first, replayed = repository.save_vocabulary_occurrence_event(
        idempotency_key="occurrence-key", payload_sha256="digest-1",
        response_json=response, created_at="now",
    )
    assert first == json.loads(response) and replayed is False
    stored = repository.get_vocabulary_occurrence_event("occurrence-key")
    assert stored == ("digest-1", response)
    replay, replayed = repository.save_vocabulary_occurrence_event(
        idempotency_key="occurrence-key", payload_sha256="digest-1",
        response_json=response, created_at="later",
    )
    assert replay == first and replayed is True
    with pytest.raises(IdempotencyConflictError):
        repository.save_vocabulary_occurrence_event(
            idempotency_key="occurrence-key", payload_sha256="digest-2",
            response_json=response, created_at="later",
        )


def test_occurrence_delete_and_source_foreign_key_actions(tmp_path: Path):
    repository = CaeliaeReadRepository(tmp_path / "foreign-keys.sqlite3")
    with repository.connection() as connection:
        _insert_paper_and_session(connection)
        _insert_entry(connection, "vocab-1", "trace")
        connection.commit()
    occurrence = repository.create_vocabulary_occurrence(
        occurrence_id="occurrence-1", vocabulary_id="vocab-1", surface_form="trace",
        paper_id="paper-1", session_id="session-1", page_number=1,
        context_excerpt=None, selection_json=None, created_at="now",
    )
    with repository.connection() as connection:
        connection.execute("DELETE FROM reading_sessions WHERE session_id='session-1'")
        connection.commit()
    after_session = repository.get_vocabulary_occurrence(occurrence.occurrence_id)
    assert after_session is not None and after_session.session_id is None and after_session.paper_id == "paper-1"
    with repository.connection() as connection:
        connection.execute("DELETE FROM papers WHERE paper_id='paper-1'")
        connection.commit()
    after_paper = repository.get_vocabulary_occurrence(occurrence.occurrence_id)
    assert after_paper is not None and after_paper.paper_id is None
    assert repository.delete_vocabulary("vocab-1") is True
    assert repository.get_vocabulary_occurrence(occurrence.occurrence_id) is None


def test_v10_failure_rolls_back_schema_and_backfill(tmp_path: Path):
    database = tmp_path / "rollback.sqlite3"
    connection = _bootstrap(database, 9)
    _insert_entry(connection, "vocab-1", "traces")
    connection.execute("CREATE TABLE vocabulary_occurrences (sentinel INTEGER)")
    connection.commit()

    with pytest.raises(sqlite3.OperationalError):
        migrate(connection)

    columns = {row[1] for row in connection.execute("PRAGMA table_info(vocabulary_entries)")}
    assert "lemma" not in columns and "entry_type" not in columns
    assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 9
    assert connection.execute("SELECT count(*) FROM vocabulary_entries").fetchone()[0] == 1
    connection.close()
