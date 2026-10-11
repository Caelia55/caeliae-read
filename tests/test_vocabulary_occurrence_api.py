from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from conftest import state_payload


def selection_for(session: dict[str, str], *, text: str, page: int = 2) -> dict[str, object]:
    return state_payload(session, event_id=str(uuid4()), page_number=page)["selection"] | {"exact_text": text}


def from_selection_payload(session: dict[str, str], *, key: str, text: str, term: str | None = None, **extra: object) -> dict[str, object]:
    return {
        "term": term or text,
        "selection": selection_for(session, text=text),
        "examples": [],
        "tags": [],
        "idempotency_key": key,
        **extra,
    }


def test_create_detail_replay_and_conflict_are_occurrence_aware(client: TestClient, session: dict[str, str]):
    payload = from_selection_payload(session, key="occ-create-key", text="traces", term="trace", lemma="trace")
    first = client.post("/api/vocabulary/from-selection", json=payload)
    assert first.status_code == 201
    first_body = first.json()
    entry = first_body["entry"]
    occurrence = first_body["occurrence"]
    assert entry["lemma"] == "trace"
    assert entry["entry_type"] == "word"
    assert entry["occurrence_count"] == 1
    assert occurrence["surface_form"] == "traces"
    assert occurrence["page_number"] == 2
    assert occurrence["selection"]["exact_text"] == "traces"

    detail = client.get(f"/api/vocabulary/{entry['vocabulary_id']}")
    assert detail.status_code == 200
    assert detail.json()["entry"]["occurrence_count"] == 1
    assert detail.json()["occurrences"][0]["occurrence_id"] == occurrence["occurrence_id"]

    replay = client.post("/api/vocabulary/from-selection", json=payload)
    assert replay.status_code == 201
    assert replay.json()["replayed"] is True
    assert replay.json()["occurrence"]["occurrence_id"] == occurrence["occurrence_id"]
    assert client.get(f"/api/vocabulary/{entry['vocabulary_id']}").json()["entry"]["occurrence_count"] == 1

    conflict = client.post("/api/vocabulary/from-selection", json=payload | {"term": "other"})
    assert conflict.status_code == 409


def test_attach_existing_and_append_endpoint_do_not_mutate_entry_or_mirror(client: TestClient, session: dict[str, str]):
    initial_payload = from_selection_payload(session, key="occ-initial-key", text="trace", term="trace", lemma="trace")
    initial = client.post("/api/vocabulary/from-selection", json=initial_payload).json()
    entry_id = initial["entry"]["vocabulary_id"]
    original_source = initial["entry"]["source_selection"]

    attach_payload = from_selection_payload(session, key="occ-attach-key", text="traces", term="changed display") | {"vocabulary_id": entry_id, "lemma": "different", "definition": "must not overwrite"}
    attached = client.post("/api/vocabulary/from-selection", json=attach_payload)
    assert attached.status_code == 201
    attached_body = attached.json()
    assert attached_body["attached"] is True
    assert attached_body["created"] is False
    assert attached_body["entry"]["term"] == "trace"
    assert attached_body["entry"]["lemma"] == "trace"
    assert attached_body["entry"]["source_selection"] == original_source
    assert attached_body["entry"]["occurrence_count"] == 2
    assert attached_body["occurrence"]["surface_form"] == "traces"

    replay = client.post("/api/vocabulary/from-selection", json=attach_payload)
    assert replay.status_code == 201 and replay.json()["replayed"] is True
    assert client.get(f"/api/vocabulary/{entry_id}").json()["entry"]["occurrence_count"] == 2

    appended = client.post(
        f"/api/vocabulary/{entry_id}/occurrences",
        json={"selection": selection_for(session, text="tracing", page=3), "idempotency_key": "occ-endpoint-key"},
    )
    assert appended.status_code == 201
    assert appended.json()["occurrence"]["surface_form"] == "tracing"
    assert client.get(f"/api/vocabulary/{entry_id}").json()["entry"]["occurrence_count"] == 3

    missing = client.post(
        f"/api/vocabulary/{uuid4()}/occurrences",
        json={"selection": selection_for(session, text="missing"), "idempotency_key": "occ-missing-key"},
    )
    assert missing.status_code == 404


def test_entry_type_is_inferred_once_and_update_requires_explicit_change(client: TestClient):
    created = client.post("/api/vocabulary", json={"term": "latent representation", "examples": [], "tags": [], "idempotency_key": "phrase-entry-key"}).json()["entry"]
    assert created["entry_type"] == "phrase"
    changed = client.put(f"/api/vocabulary/{created['vocabulary_id']}", json={"term": "latent", "examples": [], "tags": []})
    assert changed.status_code == 200 and changed.json()["entry"]["entry_type"] == "phrase"
    explicit = client.put(f"/api/vocabulary/{created['vocabulary_id']}", json={"term": "latent", "entry_type": "word", "lemma": "latent", "examples": [], "tags": []})
    assert explicit.status_code == 200 and explicit.json()["entry"]["entry_type"] == "word"


def test_search_matches_lemma_surface_context_and_returns_each_entry_once(client: TestClient, session: dict[str, str]):
    payload = from_selection_payload(session, key="search-occ-key", text="traces", term="trace", lemma="trace", tags=["morphology"])
    created = client.post("/api/vocabulary/from-selection", json=payload).json()["entry"]
    entry_id = created["vocabulary_id"]
    client.post(
        f"/api/vocabulary/{entry_id}/occurrences",
        json={"selection": selection_for(session, text="trace", page=3), "idempotency_key": "search-occ-second"},
    )
    for query in ("trace", "traces", "morphology", "acceptance page"):
        result = client.get("/api/vocabulary", params={"search": query}).json()["entries"]
        assert [item["vocabulary_id"] for item in result].count(entry_id) == 1


def test_selection_does_not_auto_merge_words_or_phrases(client: TestClient, session: dict[str, str]):
    first = client.post("/api/vocabulary/from-selection", json=from_selection_payload(session, key="separate-trace-key", text="trace", term="trace", lemma="trace")).json()["entry"]
    second = client.post("/api/vocabulary/from-selection", json=from_selection_payload(session, key="separate-traces-key", text="traces", term="traces", lemma="trace")).json()["entry"]
    same_surface = client.post("/api/vocabulary/from-selection", json=from_selection_payload(session, key="separate-surface-key", text="trace", term="another trace", lemma="trace")).json()["entry"]
    phrase = client.post("/api/vocabulary/from-selection", json=from_selection_payload(session, key="separate-phrase-key", text="be associated with", term="be associated with")).json()["entry"]
    assert first["vocabulary_id"] != second["vocabulary_id"]
    assert first["vocabulary_id"] != same_surface["vocabulary_id"]
    assert first["lemma"] == second["lemma"] == "trace"
    assert phrase["entry_type"] == "phrase"
    assert phrase["term"] == "be associated with"

    duplicate_occurrence = client.post(
        f"/api/vocabulary/{first['vocabulary_id']}/occurrences",
        json={"selection": selection_for(session, text="trace"), "idempotency_key": "separate-duplicate-occ-key"},
    )
    assert duplicate_occurrence.status_code == 201
    assert client.get(f"/api/vocabulary/{first['vocabulary_id']}").json()["entry"]["occurrence_count"] == 2


def test_source_deletion_keeps_occurrence_detail_readable(client: TestClient, service, session: dict[str, str]):
    created = client.post("/api/vocabulary/from-selection", json=from_selection_payload(session, key="delete-source-key", text="trace")).json()["entry"]
    with service.repository.connection() as connection:
        connection.execute("DELETE FROM papers WHERE paper_id=?", (session["paper_id"],))
        connection.commit()
    detail = client.get(f"/api/vocabulary/{created['vocabulary_id']}")
    assert detail.status_code == 200
    assert detail.json()["occurrences"][0]["paper_id"] is None
    assert detail.json()["occurrences"][0]["paper_title"] is None


def test_create_occurrence_failure_rolls_back_entry_and_event(client: TestClient, service, session: dict[str, str], monkeypatch: pytest.MonkeyPatch):
    def fail(*args, **kwargs):
        raise RuntimeError("injected occurrence failure")

    monkeypatch.setattr(service.repository, "_insert_vocabulary_occurrence", fail)
    with pytest.raises(RuntimeError):
        service.create_vocabulary(from_selection_payload(session, key="rollback-occ-key", text="trace"), selection_for(session, text="trace"))
    with service.repository.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM vocabulary_entries").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM vocabulary_occurrences").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM vocabulary_events").fetchone()[0] == 0


def test_create_event_failure_rolls_back_entry_and_occurrence(client: TestClient, service, session: dict[str, str], monkeypatch: pytest.MonkeyPatch):
    def fail(*args, **kwargs):
        raise RuntimeError("injected event failure")

    monkeypatch.setattr(service.repository, "_insert_vocabulary_event", fail)
    with pytest.raises(RuntimeError):
        service.create_vocabulary(from_selection_payload(session, key="rollback-event-key", text="trace"), selection_for(session, text="trace"))
    with service.repository.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM vocabulary_entries").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM vocabulary_occurrences").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM vocabulary_events").fetchone()[0] == 0


def test_occurrence_contract_is_in_openapi(client: TestClient):
    schema = client.get("/openapi.json").json()
    assert "/api/vocabulary/{vocabulary_id}" in schema["paths"]
    assert "/api/vocabulary/{vocabulary_id}/occurrences" in schema["paths"]
    vocabulary_input = schema["components"]["schemas"]["VocabularyFromSelectionInput"]["properties"]
    assert "vocabulary_id" in vocabulary_input and "lemma" in vocabulary_input and "entry_type" in vocabulary_input
