from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from caeliae_read.storage.migrations import MIGRATIONS, migrate


def task_payload(paper_id: str, *, key: str = "reading-task-key") -> dict[str, object]:
    return {
        "paper_id": paper_id,
        "title": "理解核心论点",
        "instructions": "先读完材料，再回答问题。",
        "status": "active",
        "origin_kind": "manual",
        "questions": [
            {"ordinal": 1, "question_type": "short_text", "prompt": "核心论点是什么？"},
            {
                "ordinal": 2,
                "question_type": "single_choice",
                "prompt": "材料属于哪一类？",
                "choices": [{"id": "paper", "value": "论文"}, {"id": "other", "value": "其他"}],
            },
        ],
        "idempotency_key": key,
    }


def create_task(client: TestClient, paper_id: str, *, key: str = "reading-task-key") -> dict[str, object]:
    response = client.post("/api/reading/tasks", json=task_payload(paper_id, key=key))
    assert response.status_code == 201, response.text
    return response.json()


def test_reading_task_create_list_get_and_idempotency(client: TestClient, uploaded: dict[str, object]):
    first = create_task(client, str(uploaded["paper_id"]))
    replay = client.post("/api/reading/tasks", json=task_payload(str(uploaded["paper_id"])))
    assert replay.status_code == 201 and replay.json()["replayed"] is True
    task_id = first["task"]["task_id"]
    listed = client.get("/api/reading/tasks", params={"status": "active"})
    assert listed.status_code == 200
    assert listed.json()["tasks"][0]["task_id"] == task_id
    assert listed.json()["tasks"][0]["questions_count"] == 2
    detail = client.get(f"/api/reading/tasks/{task_id}")
    assert detail.status_code == 200
    assert [question["ordinal"] for question in detail.json()["questions"]] == [1, 2]


def test_reading_question_validation_and_paper_fk(client: TestClient, uploaded: dict[str, object]):
    base = task_payload(str(uploaded["paper_id"]), key="invalid-reading-key")
    duplicate = base | {"questions": [base["questions"][0], base["questions"][0]]}  # type: ignore[index]
    assert client.post("/api/reading/tasks", json=duplicate).status_code == 422
    invalid_choice = base | {"questions": [{"ordinal": 1, "question_type": "single_choice", "prompt": "Pick", "choices": [{"id": "same", "value": "A"}, {"id": "same", "value": "B"}]}]}
    assert client.post("/api/reading/tasks", json=invalid_choice).status_code == 422
    missing_paper = task_payload(str(uuid4()), key="missing-paper-key")
    assert client.post("/api/reading/tasks", json=missing_paper).status_code == 404


def test_reading_open_reuses_task_session_and_does_not_change_papers_session(client: TestClient, uploaded: dict[str, object], session: dict[str, str]):
    created = create_task(client, str(uploaded["paper_id"]), key="open-session-key")
    task_id = created["task"]["task_id"]
    first = client.post(f"/api/reading/tasks/{task_id}/open")
    second = client.post(f"/api/reading/tasks/{task_id}/open")
    assert first.status_code == second.status_code == 200
    assert first.json()["session_id"] == second.json()["session_id"]
    assert first.json()["session_id"] != session["session_id"]


def test_reading_draft_submit_feedback_lifecycle(client: TestClient, uploaded: dict[str, object]):
    created = create_task(client, str(uploaded["paper_id"]), key="lifecycle-task-key")
    task_id = created["task"]["task_id"]
    questions = created["questions"]
    draft_payload = {
        "revision": 0,
        "answers": [
            {"question_id": questions[0]["question_id"], "answer_text": "核心论点"},
            {"question_id": questions[1]["question_id"], "selected_choice": "paper"},
        ],
        "client_event_id": str(uuid4()),
    }
    draft = client.put(f"/api/reading/tasks/{task_id}/submission", json=draft_payload)
    assert draft.status_code == 200 and draft.json()["submission"]["revision"] == 1
    stale = client.put(f"/api/reading/tasks/{task_id}/submission", json={**draft_payload, "client_event_id": str(uuid4())})
    assert stale.status_code == 409
    submit_payload = {"revision": 1, "client_event_id": str(uuid4())}
    submitted = client.post(f"/api/reading/tasks/{task_id}/submission/submit", json=submit_payload)
    assert submitted.status_code == 200
    replay = client.post(f"/api/reading/tasks/{task_id}/submission/submit", json=submit_payload)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    blocked = client.put(f"/api/reading/tasks/{task_id}/submission", json={**draft_payload, "revision": 2, "client_event_id": str(uuid4())})
    assert blocked.status_code == 409
    feedback = client.post(f"/api/reading/tasks/{task_id}/feedback", json={"source_kind": "manual", "feedback_text": "回答清楚。", "question_feedback": {questions[0]["question_id"]: "可以继续补充证据。"}})
    assert feedback.status_code == 201
    assert feedback.json()["feedback"]["revision"] == 1
    assert client.get(f"/api/reading/tasks/{task_id}/submission").json()["submission"]["status"] == "reviewed"


def test_reading_submit_requires_all_answers(client: TestClient, uploaded: dict[str, object]):
    created = create_task(client, str(uploaded["paper_id"]), key="incomplete-task-key")
    task_id = created["task"]["task_id"]
    question = created["questions"][0]
    draft = client.put(f"/api/reading/tasks/{task_id}/submission", json={"revision": 0, "answers": [{"question_id": question["question_id"], "answer_text": "only one"}], "client_event_id": str(uuid4())})
    assert draft.status_code == 200
    assert client.post(f"/api/reading/tasks/{task_id}/submission/submit", json={"revision": 1, "client_event_id": str(uuid4())}).status_code == 422


def test_v8_to_v9_migration_preserves_existing_rows(tmp_path: Path):
    database = tmp_path / "existing.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    for version, sql in MIGRATIONS[:8]:
        connection.executescript(sql)
        connection.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, 'old')", (version,))
    connection.execute("INSERT INTO papers(paper_id, content_sha256, original_filename, storage_key, size_bytes, mime_type, page_count, created_at) VALUES ('paper', 'hash', 'paper.pdf', 'paper.pdf', 1, 'application/pdf', 1, 'old')")
    connection.commit()
    migrate(connection)
    assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 9
    assert connection.execute("SELECT original_filename FROM papers WHERE paper_id='paper'").fetchone()[0] == "paper.pdf"
    assert connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reading_tasks'").fetchone() is not None
    assert connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='summary_notes'").fetchone() is not None
    connection.close()
