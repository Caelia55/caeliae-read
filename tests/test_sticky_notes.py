from uuid import uuid4

from fastapi.testclient import TestClient


def sticky_payload(session, **overrides):
    return {
        "paper_id": session["paper_id"],
        "page": 1,
        "x": 0.25,
        "y": 0.75,
        "text": "read this later",
        "style_key": "secondary",
        **overrides,
    }


def test_sticky_note_uses_normalized_point_and_supports_edit_delete(client: TestClient, session):
    response = client.post("/api/sticky-notes", json=sticky_payload(session))
    assert response.status_code == 201
    created = response.json()["sticky_note"]
    assert created["paper_id"] == session["paper_id"]
    assert created["page"] == 1 and created["x"] == 0.25 and created["y"] == 0.75
    assert created["style_key"] == "secondary"

    listed = client.get("/api/sticky-notes", params={"paper_id": session["paper_id"], "page": 1}).json()["sticky_notes"]
    assert listed == [created]
    updated = client.put(f"/api/sticky-notes/{created['id']}", json={"x": 0.9, "y": 0.1, "text": "updated", "style_key": "tertiary"})
    assert updated.status_code == 200
    assert updated.json()["sticky_note"]["style_key"] == "tertiary"
    assert updated.json()["sticky_note"]["x"] == 0.9 and updated.json()["sticky_note"]["y"] == 0.1
    preserved = client.put(f"/api/sticky-notes/{created['id']}", json={"text": "updated without moving", "style_key": "primary"})
    assert preserved.status_code == 200
    assert preserved.json()["sticky_note"]["x"] == 0.9 and preserved.json()["sticky_note"]["y"] == 0.1
    assert client.delete(f"/api/sticky-notes/{created['id']}").json()["deleted"] is True
    assert client.get("/api/sticky-notes", params={"paper_id": session["paper_id"]}).json()["sticky_notes"] == []


def test_sticky_note_defaults_and_rejects_invalid_geometry_or_style(client: TestClient, session):
    default = client.post("/api/sticky-notes", json=sticky_payload(session, style_key=None)).json()["sticky_note"]
    assert default["style_key"] == "primary"
    invalid = client.post("/api/sticky-notes", json=sticky_payload(session, x=1.1, style_key="#fff"))
    assert invalid.status_code == 400
    invalid_id = client.put(f"/api/sticky-notes/{uuid4()}", json={"text": "x", "style_key": "primary"})
    assert invalid_id.status_code == 400
