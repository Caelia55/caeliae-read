from uuid import uuid4

from fastapi.testclient import TestClient

from conftest import state_payload


def entry_payload(session: dict[str, str], key: str | None = None) -> dict[str, object]:
    return {
        "term": "Liminal", "definition": "处于边界之间的", "part_of_speech": "adjective",
        "pronunciation": "/ˈlɪmɪnl/", "examples": ["A liminal space"], "notes": "Keep in context",
        "tags": ["reading", "space"], "source_paper_id": session["paper_id"],
        "source_session_id": session["session_id"], "source_page_number": 2,
        "source_selection": None, "idempotency_key": key or str(uuid4()),
    }


def test_vocabulary_crud_and_exports(client: TestClient, session: dict[str, str]):
    created = client.post("/api/vocabulary", json=entry_payload(session)).json()
    entry = created["entry"]
    assert entry["normalized_term"] == "liminal"
    listed = client.get("/api/vocabulary", params={"search": "lim"}).json()["entries"]
    assert listed[0]["vocabulary_id"] == entry["vocabulary_id"]
    changed = client.put(f"/api/vocabulary/{entry['vocabulary_id']}", json={"term": "Liminality", "definition": "boundary state", "examples": [], "tags": []})
    assert changed.status_code == 200 and changed.json()["entry"]["normalized_term"] == "liminality"
    assert "Liminality" in client.get("/api/vocabulary/export", params={"format": "csv"}).text
    assert client.get("/api/vocabulary/export", params={"format": "json"}).json()["entries"][0]["term"] == "Liminality"
    assert client.delete(f"/api/vocabulary/{entry['vocabulary_id']}").json() == {"deleted": True}


def test_vocabulary_idempotency_and_selection_contract(client: TestClient, session: dict[str, str]):
    selection = state_payload(session, event_id=str(uuid4()))["selection"]
    payload = entry_payload(session, "stable-vocabulary-key") | {"term": "Threshold"}
    for field in ("source_paper_id", "source_session_id", "source_page_number", "source_selection"):
        payload.pop(field)
    first = client.post("/api/vocabulary/from-selection", json={**payload, "selection": selection}).json()
    replay = client.post("/api/vocabulary/from-selection", json={**payload, "selection": selection}).json()
    assert first["entry"]["source_selection"]["exact_text"] == "Selected passage"
    paper = client.get("/api/papers").json()["papers"]
    paper_title = next(item["original_filename"] for item in paper if item["paper_id"] == session["paper_id"])
    assert first["entry"]["source_paper_title"] == paper_title
    assert first["entry"]["source_page_number"] == selection["page_number"]
    assert first["entry"]["source_selection"]["prefix"]
    assert first["entry"]["source_selection"]["suffix"]
    assert first["entry"]["definition"] == payload["definition"]
    assert first["entry"]["notes"] == payload["notes"]
    assert replay["replayed"] is True
    conflict = client.post("/api/vocabulary/from-selection", json={**payload, "term": "Other", "selection": selection})
    assert conflict.status_code == 409


def test_vocabulary_rejects_unpaired_source(client: TestClient):
    response = client.post("/api/vocabulary", json={"term": "word", "source_paper_id": str(uuid4()), "idempotency_key": "source-key-123"})
    assert response.status_code == 400


def test_vocabulary_review_result_is_minimal_and_scoped(client: TestClient):
    created = client.post("/api/vocabulary", json={"term": "Reviewable", "definition": "ready", "examples": [], "tags": [], "idempotency_key": "review-entry-key"}).json()["entry"]
    response = client.post("/api/vocabulary/reviews", json={"vocabulary_id": created["vocabulary_id"], "review_session_id": str(uuid4()), "rating": "fuzzy"})
    assert response.status_code == 201
    review = response.json()["review"]
    assert review["vocabulary_id"] == created["vocabulary_id"]
    assert review["rating"] == "fuzzy" and review["mode"] == "local"
