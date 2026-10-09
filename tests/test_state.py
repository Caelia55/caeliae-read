from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from conftest import state_payload


def test_state_round_trip_selection_and_refresh_restore(
    client: TestClient, session: dict[str, str]
) -> None:
    payload = state_payload(session, event_id=str(uuid4()))
    saved = client.put(f"/api/sessions/{session['session_id']}/state", json=payload)
    assert saved.status_code == 200
    assert saved.json()["replayed"] is False

    context = client.get(f"/api/sessions/{session['session_id']}/context").json()
    assert context["page_number"] == 2
    assert context["checkpoint"]["revision"] == 1
    assert context["selection"] == payload["selection"]
    assert client.get("/api/reading/current").json() == context


def test_client_event_is_idempotent_and_payload_bound(
    client: TestClient, session: dict[str, str]
) -> None:
    event_id = str(uuid4())
    payload = state_payload(session, event_id=event_id)
    first = client.put(f"/api/sessions/{session['session_id']}/state", json=payload)
    replay = client.put(f"/api/sessions/{session['session_id']}/state", json=payload)
    assert first.status_code == replay.status_code == 200
    assert replay.json()["replayed"] is True

    changed = payload | {"page_text": "changed"}
    conflict = client.put(f"/api/sessions/{session['session_id']}/state", json=changed)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "client_event_conflict"


def test_revision_cannot_stale_or_skip(
    client: TestClient, session: dict[str, str]
) -> None:
    endpoint = f"/api/sessions/{session['session_id']}/state"
    assert client.put(endpoint, json=state_payload(session, event_id=str(uuid4()))).status_code == 200

    stale = state_payload(session, event_id=str(uuid4()), revision=1)
    skipped = state_payload(session, event_id=str(uuid4()), revision=3)
    for payload in (stale, skipped):
        response = client.put(endpoint, json=payload)
        assert response.status_code == 409
        assert response.json()["detail"]["current_revision"] == 1


def test_page_text_is_bounded_and_extra_fields_are_rejected(
    client: TestClient, session: dict[str, str]
) -> None:
    endpoint = f"/api/sessions/{session['session_id']}/state"
    too_long = state_payload(session, event_id=str(uuid4())) | {"page_text": "x" * 4001}
    assert client.put(endpoint, json=too_long).status_code == 422
    extra = state_payload(session, event_id=str(uuid4())) | {"whole_pdf": "forbidden"}
    assert client.put(endpoint, json=extra).status_code == 422

    outside_paper = state_payload(
        session, event_id=str(uuid4()), page_number=3, with_selection=False
    )
    response = client.put(endpoint, json=outside_paper)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "validation_error"


def test_selection_identity_and_revision_must_match(
    client: TestClient, session: dict[str, str]
) -> None:
    endpoint = f"/api/sessions/{session['session_id']}/state"
    payload = state_payload(session, event_id=str(uuid4()))
    payload["selection"]["revision"] = 2  # type: ignore[index]
    response = client.put(endpoint, json=payload)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "validation_error"
