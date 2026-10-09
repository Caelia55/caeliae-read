import sqlite3
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from caeliae_read.storage.migrations import MIGRATIONS, migrate


def summary_payload(paper_id: str, **overrides: object) -> dict[str, object]:
    return {
        "paper_id": paper_id,
        "page_number": 1,
        "normalized_y": 0.42,
        "text": "This section introduces the main claim.",
        **overrides,
    }


def test_summary_note_crud_is_paper_scoped_and_ordered(client: TestClient, uploaded: dict[str, object]):
    paper_id = str(uploaded["paper_id"])
    first_response = client.post("/api/summary-notes", json=summary_payload(paper_id, normalized_y=0.8))
    second_response = client.post("/api/summary-notes", json=summary_payload(paper_id, normalized_y=0.2, text="Earlier idea."))
    assert first_response.status_code == 201
    assert second_response.status_code == 201
    first = first_response.json()["summary_note"]
    second = second_response.json()["summary_note"]
    assert first["paper_id"] == paper_id
    assert first["page_number"] == 1
    assert first["normalized_y"] == 0.8
    assert "session_id" not in first
    assert "normalized_quads" not in first

    listed = client.get("/api/summary-notes", params={"paper_id": paper_id}).json()["summary_notes"]
    assert [note["summary_note_id"] for note in listed] == [second["summary_note_id"], first["summary_note_id"]]

    updated = client.put(
        f"/api/summary-notes/{first['summary_note_id']}",
        json={"text": "The section's claim, in brief."},
    )
    assert updated.status_code == 200
    assert updated.json()["summary_note"]["text"] == "The section's claim, in brief."
    assert updated.json()["summary_note"]["normalized_y"] == 0.8
    assert client.delete(f"/api/summary-notes/{second['summary_note_id']}").json()["deleted"] is True
    assert len(client.get("/api/summary-notes", params={"paper_id": paper_id}).json()["summary_notes"]) == 1


def test_summary_note_validates_position_text_page_and_payload_shape(client: TestClient, uploaded: dict[str, object]):
    paper_id = str(uploaded["paper_id"])
    for overrides in (
        {"page_number": 0},
        {"normalized_y": -0.01},
        {"normalized_y": 1.01},
        {"text": "   "},
        {"text": "x" * 2_001},
        {"unexpected": True},
    ):
        response = client.post("/api/summary-notes", json=summary_payload(paper_id, **overrides))
        assert response.status_code == 422

    outside = client.post("/api/summary-notes", json=summary_payload(paper_id, page_number=int(uploaded["page_count"]) + 1))
    assert outside.status_code == 400
    assert client.get("/api/summary-notes", params={"paper_id": str(uuid4())}).status_code == 404
    assert client.put(f"/api/summary-notes/{uuid4()}", json={"text": "missing"}).status_code == 400


def test_summary_note_paper_delete_cascades(tmp_path: Path, client: TestClient, uploaded: dict[str, object], service):
    paper_id = str(uploaded["paper_id"])
    created = client.post("/api/summary-notes", json=summary_payload(paper_id)).json()["summary_note"]
    with service.repository.connection() as connection:
        connection.execute("DELETE FROM papers WHERE paper_id=?", (paper_id,))
        connection.commit()
        assert connection.execute("SELECT count(*) FROM summary_notes WHERE summary_note_id=?", (created["summary_note_id"],)).fetchone()[0] == 0


def test_v8_to_v9_migration_is_additive_and_preserves_paper(tmp_path: Path):
    database = tmp_path / "existing.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    for version, sql in MIGRATIONS[:8]:
        connection.executescript(sql)
        connection.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, 'old')", (version,))
    connection.execute(
        "INSERT INTO papers(paper_id, content_sha256, original_filename, storage_key, size_bytes, mime_type, page_count, created_at) VALUES ('paper', 'hash', 'paper.pdf', 'paper.pdf', 1, 'application/pdf', 1, 'old')"
    )
    connection.commit()
    migrate(connection)
    assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 9
    assert connection.execute("SELECT original_filename FROM papers WHERE paper_id='paper'").fetchone()[0] == "paper.pdf"
    columns = {row[1] for row in connection.execute("PRAGMA table_info(summary_notes)")}
    assert columns == {"summary_note_id", "paper_id", "page_number", "normalized_y", "text", "created_at", "updated_at"}
    connection.close()
