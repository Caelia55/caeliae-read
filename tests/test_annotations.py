from uuid import uuid4

from fastapi.testclient import TestClient

from conftest import state_payload


def annotation_payload(session, key=None):
    return {
        "paper_id": session["paper_id"], "session_id": session["session_id"], "page_number": 2,
        "exact_text": "Selected passage", "prefix": "before ", "suffix": " after",
        "normalized_quads": [{"x": .1, "y": .2, "width": .3, "height": .04}],
        "page_width": 612, "page_height": 792, "rotation": 0,
        "note": "question", "remember": False, "idempotency_key": key or str(uuid4()),
    }


def test_user_and_assistant_authors_are_server_owned(client: TestClient, session):
    client.put(f"/api/sessions/{session['session_id']}/state", json=state_payload(session, event_id=str(uuid4())))
    user = annotation_payload(session) | {"author": "assistant"}
    created = client.post("/api/annotations/user", json=user)
    assert created.status_code == 201 and created.json()["annotation"]["author"] == "user"
    assert created.json()["annotation"]["style_key"] == "primary"
    assistant = annotation_payload(session) | {"author": "user", "note": "answer", "remember": True}
    created_ai = client.post("/api/annotations", json=assistant)
    assert created_ai.status_code == 201 and created_ai.json()["annotation"]["author"] == "assistant"
    assert created_ai.json()["annotation"]["style_key"] is None


def test_user_annotation_style_key_is_semantic_and_validated(client: TestClient, session):
    client.put(f"/api/sessions/{session['session_id']}/state", json=state_payload(session, event_id=str(uuid4())))
    secondary = client.post("/api/annotations/user", json=annotation_payload(session) | {"style_key": "secondary"}).json()["annotation"]
    assert secondary["style_key"] == "secondary"
    invalid = client.post("/api/annotations/user", json=annotation_payload(session) | {"style_key": "#ff00aa"})
    assert invalid.status_code == 400


def test_user_annotation_update_changes_style_or_note_without_changing_geometry(client: TestClient, session):
    client.put(f"/api/sessions/{session['session_id']}/state", json=state_payload(session, event_id=str(uuid4())))
    payload = annotation_payload(session) | {"style_key": "primary", "note": "first note"}
    created = client.post("/api/annotations/user", json=payload).json()["annotation"]
    changed = client.put(
        f"/api/annotations/{created['annotation_id']}",
        json={"session_id": session["session_id"], "style_key": "secondary"},
    )
    assert changed.status_code == 200
    updated = changed.json()["annotation"]
    assert updated["style_key"] == "secondary"
    assert updated["note"] == "first note"
    assert updated["normalized_quads"] == created["normalized_quads"]
    assert updated["exact_text"] == created["exact_text"]

    tertiary = client.put(
        f"/api/annotations/{created['annotation_id']}",
        json={"session_id": session["session_id"], "style_key": "tertiary"},
    )
    assert tertiary.status_code == 200
    assert tertiary.json()["annotation"]["style_key"] == "tertiary"
    assert tertiary.json()["annotation"]["normalized_quads"] == created["normalized_quads"]

    note_changed = client.put(
        f"/api/annotations/{created['annotation_id']}",
        json={"session_id": session["session_id"], "note": "edited note"},
    )
    assert note_changed.status_code == 200
    assert note_changed.json()["annotation"]["note"] == "edited note"

    assistant = client.post("/api/annotations", json=annotation_payload(session) | {"note": "AI"}).json()["annotation"]
    rejected = client.put(
        f"/api/annotations/{assistant['annotation_id']}",
        json={"session_id": session["session_id"], "style_key": "tertiary"},
    )
    assert rejected.status_code == 400


def test_user_annotation_mark_type_round_trip_preserves_geometry(client: TestClient, session):
    client.put(f"/api/sessions/{session['session_id']}/state", json=state_payload(session, event_id=str(uuid4())))
    payload = annotation_payload(session) | {"style_key": "secondary", "mark_type": "highlight"}
    created = client.post("/api/annotations/user", json=payload).json()["annotation"]
    assert created["mark_type"] == "highlight"
    geometry = created["normalized_quads"]
    listed = client.get("/api/annotations", params={"paper_id": session["paper_id"]}).json()["annotations"]
    assert next(item for item in listed if item["annotation_id"] == created["annotation_id"])["mark_type"] == "highlight"

    changed = client.put(
        f"/api/annotations/{created['annotation_id']}",
        json={"session_id": session["session_id"], "mark_type": "underline"},
    )
    assert changed.status_code == 200
    updated = changed.json()["annotation"]
    assert updated["mark_type"] == "underline"
    assert updated["style_key"] == "secondary"
    assert updated["normalized_quads"] == geometry
    assert updated["exact_text"] == created["exact_text"]

    back_to_highlight = client.put(
        f"/api/annotations/{created['annotation_id']}",
        json={"session_id": session["session_id"], "mark_type": "highlight"},
    )
    assert back_to_highlight.status_code == 200
    assert back_to_highlight.json()["annotation"]["mark_type"] == "highlight"
    back_to_underline = client.put(
        f"/api/annotations/{created['annotation_id']}",
        json={"session_id": session["session_id"], "mark_type": "underline"},
    )
    assert back_to_underline.status_code == 200
    assert back_to_underline.json()["annotation"]["mark_type"] == "underline"

    invalid = client.put(
        f"/api/annotations/{created['annotation_id']}",
        json={"session_id": session["session_id"], "mark_type": "marker"},
    )
    assert invalid.status_code == 400

    assistant = client.post("/api/annotations", json=annotation_payload(session)).json()["annotation"]
    assert assistant["mark_type"] == "underline"


def test_idempotency_persists_once_and_user_cannot_delete_assistant(client: TestClient, session):
    client.put(f"/api/sessions/{session['session_id']}/state", json=state_payload(session, event_id=str(uuid4())))
    payload = annotation_payload(session, "stable-key-123")
    first = client.post("/api/annotations", json=payload).json()
    replay = client.post("/api/annotations", json=payload).json()
    assert replay["replayed"] is True
    assert len(client.get("/api/annotations", params={"paper_id": session["paper_id"]}).json()["annotations"]) == 1
    assert client.delete(f"/api/annotations/{first['annotation']['annotation_id']}", params={"session_id": session["session_id"]}).json()["deleted"] is False


def test_user_annotation_can_be_deleted_and_anchor_round_trips(client: TestClient, session):
    client.put(f"/api/sessions/{session['session_id']}/state", json=state_payload(session, event_id=str(uuid4())))
    payload = annotation_payload(session)
    result = client.post("/api/annotations/user", json=payload).json()["annotation"]
    listed = client.get("/api/annotations", params={"paper_id": session["paper_id"], "page_number": 2}).json()["annotations"]
    assert listed[0]["annotation_id"] == result["annotation_id"]
    for field in ("paper_id", "page_number", "exact_text", "prefix", "suffix", "normalized_quads", "rotation", "annotation_id", "thread_id"):
        assert listed[0][field] == result[field]
    assert client.delete(f"/api/annotations/{result['annotation_id']}", params={"session_id": session["session_id"]}).json()["deleted"] is True
    assert client.get("/api/annotations", params={"paper_id": session["paper_id"]}).json()["annotations"] == []


def test_revoke_assistant_remember_preserves_annotation_and_rejects_other_targets(client: TestClient, service, session):
    client.put(f"/api/sessions/{session['session_id']}/state", json=state_payload(session, event_id=str(uuid4())))
    remembered = client.post("/api/annotations", json=annotation_payload(session) | {"note": "keep this AI note", "remember": True}).json()["annotation"]
    response = client.post(f"/api/annotations/{remembered['annotation_id']}/remember/revoke")
    assert response.status_code == 200
    updated = response.json()["annotation"]
    assert updated["annotation_id"] == remembered["annotation_id"]
    assert updated["author"] == "assistant" and updated["remember"] is False
    for field in ("thread_id", "paper_id", "session_id", "page_number", "exact_text", "prefix", "suffix", "normalized_quads", "page_width", "page_height", "rotation", "note"):
        assert updated[field] == remembered[field]
    assert updated["created_at"] and updated["updated_at"]
    assert service.list_annotations(session["paper_id"])[0] == updated
    assert client.delete(f"/api/annotations/{remembered['annotation_id']}", params={"session_id": session["session_id"]}).json()["deleted"] is False
    assert client.post(f"/api/annotations/{remembered['annotation_id']}/remember/revoke").status_code == 400

    not_remembered = client.post("/api/annotations", json=annotation_payload(session) | {"remember": False}).json()["annotation"]
    assert client.post(f"/api/annotations/{not_remembered['annotation_id']}/remember/revoke").status_code == 400
    user = client.post("/api/annotations/user", json=annotation_payload(session)).json()["annotation"]
    assert client.post(f"/api/annotations/{user['annotation_id']}/remember/revoke").status_code == 400
