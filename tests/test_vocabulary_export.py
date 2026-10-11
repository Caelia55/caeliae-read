from __future__ import annotations

import csv
import io
from uuid import uuid4

from fastapi.testclient import TestClient

from conftest import state_payload


def selection_for(session: dict[str, str], *, text: str, page: int = 2) -> dict[str, object]:
    return state_payload(session, event_id=str(uuid4()), page_number=page)["selection"] | {"exact_text": text}


def create_source_entry(client: TestClient, session: dict[str, str], *, key: str, text: str = "traces") -> dict[str, object]:
    response = client.post(
        "/api/vocabulary/from-selection",
        json={
            "term": "traces",
            "lemma": "trace",
            "entry_type": "word",
            "definition": "语境, meaning",
            "part_of_speech": "noun",
            "tags": ["tag,one", "阅读"],
            "examples": [],
            "selection": selection_for(session, text=text),
            "idempotency_key": key,
        },
    )
    assert response.status_code == 201
    return response.json()["entry"]


def test_occurrence_aware_exports_preserve_entry_contract_and_csv_escaping(client: TestClient, session: dict[str, str]):
    entry = create_source_entry(client, session, key="export-source-key", text='traces, "quoted"\r\n中文')
    entry_id = entry["vocabulary_id"]
    appended = client.post(
        f"/api/vocabulary/{entry_id}/occurrences",
        json={
            "selection": selection_for(session, text="tracing", page=3),
            "idempotency_key": "export-variant-key",
        },
    )
    assert appended.status_code == 201

    manual = client.post(
        "/api/vocabulary",
        json={"term": "manual phrase", "examples": [], "tags": [], "idempotency_key": "export-manual-key"},
    )
    assert manual.status_code == 201

    default_json = client.get("/api/vocabulary/export", params={"format": "json", "search": "trace"})
    assert default_json.status_code == 200
    default_entries = default_json.json()["entries"]
    assert len(default_entries) == 1
    assert default_entries[0]["vocabulary_id"] == entry_id
    assert default_entries[0]["occurrence_count"] == 2

    default_csv = client.get("/api/vocabulary/export", params={"format": "csv", "search": "trace"})
    assert default_csv.status_code == 200
    default_rows = list(csv.DictReader(io.StringIO(default_csv.text, newline="")))
    assert len(default_rows) == 1
    assert default_rows[0]["vocabulary_id"] == entry_id
    assert default_rows[0]["term"] == "traces"
    assert default_csv.text.splitlines()[0].split(",")[:13] == [
        "vocabulary_id", "term", "definition", "part_of_speech", "pronunciation", "examples",
        "notes", "tags", "source_paper_id", "source_session_id", "source_page_number", "created_at", "updated_at",
    ]
    assert default_csv.text.splitlines()[0].split(",")[-3:] == ["lemma", "entry_type", "occurrence_count"]

    detailed = client.get(
        "/api/vocabulary/export",
        params={"format": "json", "detail": "occurrences", "search": "trace"},
    )
    assert detailed.status_code == 200
    detailed_entry = detailed.json()["entries"][0]
    assert detailed_entry["term"] == "traces"
    assert detailed_entry["lemma"] == "trace"
    assert len(detailed_entry["occurrences"]) == 2
    assert [item["surface_form"] for item in detailed_entry["occurrences"]] == ['traces, "quoted"\r\n中文', "tracing"]
    assert "source_selection" not in detailed_entry
    assert all("selection" not in item for item in detailed_entry["occurrences"])
    assert detailed_entry["occurrences"][0]["paper_title"] == "research-paper.pdf"

    occurrence_csv = client.get(
        "/api/vocabulary/export",
        params={"format": "csv-occurrences", "search": "trace"},
    )
    assert occurrence_csv.status_code == 200
    occurrence_rows = list(csv.DictReader(io.StringIO(occurrence_csv.text, newline="")))
    assert len(occurrence_rows) == 2
    assert [row["surface_form"] for row in occurrence_rows] == ['traces, "quoted"\r\n中文', "tracing"]
    assert occurrence_rows[0]["tags"] == "tag,one | 阅读"
    assert occurrence_rows[0]["context_excerpt"]
    assert "manual phrase" not in occurrence_csv.text


def test_occurrence_exports_survive_deleted_sources(client: TestClient, service, session: dict[str, str]):
    entry = create_source_entry(client, session, key="export-deleted-source-key", text="trace")
    with service.repository.connection() as connection:
        connection.execute("DELETE FROM papers WHERE paper_id=?", (session["paper_id"],))
        connection.commit()

    detailed = client.get(
        "/api/vocabulary/export",
        params={"format": "json", "detail": "occurrences", "search": "trace"},
    )
    assert detailed.status_code == 200
    occurrence = detailed.json()["entries"][0]["occurrences"][0]
    assert occurrence["surface_form"] == "trace"
    assert occurrence["context_excerpt"]
    assert occurrence["paper_id"] is None
    assert occurrence["paper_title"] is None

    occurrence_csv = client.get(
        "/api/vocabulary/export",
        params={"format": "csv-occurrences", "search": "trace"},
    )
    assert occurrence_csv.status_code == 200
    row = next(csv.DictReader(io.StringIO(occurrence_csv.text, newline="")))
    assert row["vocabulary_id"] == entry["vocabulary_id"]
    assert row["surface_form"] == "trace"
    assert row["paper_id"] == ""
    assert row["paper_title"] == ""


def test_occurrence_csv_omits_manual_entries(client: TestClient):
    response = client.post(
        "/api/vocabulary",
        json={"term": "manual only", "examples": [], "tags": [], "idempotency_key": "export-only-manual-key"},
    )
    assert response.status_code == 201
    export = client.get("/api/vocabulary/export", params={"format": "csv-occurrences"})
    assert export.status_code == 200
    assert list(csv.DictReader(io.StringIO(export.text, newline=""))) == []
