from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
import zipfile
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from caeliae_read.application.service import CaeliaeReadService
from caeliae_read.bibtex import portable_fallback_citation_key


FIXED_EXPORTED_AT = "2026-10-29T00:00:00Z"


def metadata_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "title": None,
        "authors": None,
        "publication_year": None,
        "venue": None,
        "doi": None,
        "arxiv_id": None,
        "citation_key": None,
    }
    body.update(overrides)
    return body


def read_archive(response) -> tuple[list[str], dict[str, bytes]]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        names = archive.namelist()
        return names, {name: archive.read(name) for name in names}


def build_optional_records(service: CaeliaeReadService, paper_id: str, session_id: str) -> None:
    annotation_id = str(uuid4())
    annotation = {
        "annotation_id": annotation_id,
        "thread_id": str(uuid4()),
        "paper_id": paper_id,
        "session_id": session_id,
        "page_number": 1,
        "exact_text": "研究文本",
        "prefix": "前文",
        "suffix": "后文",
        "normalized_quads": [{"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.04}],
        "page_width": 612,
        "page_height": 792,
        "rotation": 0,
        "note": "备注",
        "remember": True,
        "author": "assistant",
        "style_key": None,
        "mark_type": "underline",
    }
    service.repository.save_annotation(
        annotation=annotation,
        idempotency_key=f"bundle-{uuid4()}",
        payload_sha256=hashlib.sha256(b"bundle-annotation").hexdigest(),
        response_json=json.dumps({"annotation": annotation}),
        created_at="2026-10-29T00:00:01Z",
        author="assistant",
    )
    service.repository.insert_sticky_note(
        {
            "id": str(uuid4()),
            "paper_id": paper_id,
            "page": 1,
            "x": 0.4,
            "y": 0.5,
            "text": "便签",
            "style_key": "secondary",
        },
        "2026-10-29T00:00:02Z",
    )
    service.repository.insert_summary_note(
        {
            "summary_note_id": str(uuid4()),
            "paper_id": paper_id,
            "page_number": 1,
            "normalized_y": 0.6,
            "normalized_x": None,
            "text": "大意",
        },
        "2026-10-29T00:00:03Z",
    )


def database_snapshot(service: CaeliaeReadService) -> dict[str, bytes | None]:
    path = service.repository.database_path
    return {
        "db": path.read_bytes() if path.exists() else None,
        "wal": path.with_name(f"{path.name}-wal").read_bytes() if path.with_name(f"{path.name}-wal").exists() else None,
        "shm": path.with_name(f"{path.name}-shm").read_bytes() if path.with_name(f"{path.name}-shm").exists() else None,
    }


def test_minimal_bundle_has_only_required_members_and_exact_pdf(
    client: TestClient, uploaded: dict[str, object], fixture_pdf: Path
) -> None:
    response = client.get(f"/api/papers/{uploaded['paper_id']}/bundle")
    names, members = read_archive(response)

    assert names == ["manifest.json", "paper.pdf", "metadata.json"]
    assert response.headers["content-disposition"] == (
        'attachment; filename="research-paper.caeliae-paper.zip"'
    )
    assert members["paper.pdf"] == fixture_pdf.read_bytes()
    metadata = json.loads(members["metadata.json"])
    assert metadata["state"] == "absent"
    manifest = json.loads(members["manifest.json"])
    assert [item["path"] for item in manifest["files"]] == ["paper.pdf", "metadata.json"]
    assert manifest["files"][0]["sha256"] == hashlib.sha256(members["paper.pdf"]).hexdigest()
    assert uploaded["paper_id"] not in members["manifest.json"].decode()


def test_present_all_null_metadata_is_exported_without_citation(
    client: TestClient, uploaded: dict[str, object]
) -> None:
    paper_id = uploaded["paper_id"]
    assert client.put(f"/api/papers/{paper_id}/metadata", json=metadata_body()).status_code == 200

    names, members = read_archive(client.get(f"/api/papers/{paper_id}/bundle"))

    assert names == ["manifest.json", "paper.pdf", "metadata.json"]
    assert json.loads(members["metadata.json"])["state"] == "present"
    assert "citation.bib" not in names


def test_title_missing_does_not_block_bundle_but_has_no_bibtex(
    client: TestClient, uploaded: dict[str, object]
) -> None:
    paper_id = uploaded["paper_id"]
    assert client.put(
        f"/api/papers/{paper_id}/metadata",
        json=metadata_body(authors=["Alice Smith"]),
    ).status_code == 200

    names, _ = read_archive(client.get(f"/api/papers/{paper_id}/bundle"))
    assert "citation.bib" not in names


def test_bundle_uses_content_fallback_without_persisting_or_leaking_paper_id(
    client: TestClient, service: CaeliaeReadService, uploaded: dict[str, object]
) -> None:
    paper_id = uploaded["paper_id"]
    assert client.put(
        f"/api/papers/{paper_id}/metadata",
        json=metadata_body(title="Portable Study"),
    ).status_code == 200
    paper = service.get_paper(paper_id)
    source = service.repository.get_paper_bundle_source(paper_id)
    expected_key = portable_fallback_citation_key(source[2])

    before = client.get(f"/api/papers/{paper_id}/metadata").json()
    names, members = read_archive(client.get(f"/api/papers/{paper_id}/bundle"))
    after = client.get(f"/api/papers/{paper_id}/metadata").json()

    assert "citation.bib" in names
    assert members["citation.bib"].decode("utf-8").startswith(f"@misc{{{expected_key},")
    assert paper_id not in members["citation.bib"].decode("utf-8")
    assert before == after
    assert json.loads(members["manifest.json"])["paper"]["content_sha256"] == source[2]


def test_explicit_citation_key_is_preserved(
    client: TestClient, uploaded: dict[str, object]
) -> None:
    paper_id = uploaded["paper_id"]
    client.put(
        f"/api/papers/{paper_id}/metadata",
        json=metadata_body(title="Portable Study", citation_key="study2026"),
    )
    _, members = read_archive(client.get(f"/api/papers/{paper_id}/bundle"))
    assert members["citation.bib"].decode("utf-8").startswith("@misc{study2026,")


def test_optional_records_are_conditional_and_preserve_provenance(
    client: TestClient,
    service: CaeliaeReadService,
    uploaded: dict[str, object],
    session: dict[str, str],
) -> None:
    paper_id = uploaded["paper_id"]
    build_optional_records(service, paper_id, session["session_id"])
    client.put(
        f"/api/papers/{paper_id}/metadata",
        json=metadata_body(title="研究论文"),
    )

    names, members = read_archive(client.get(f"/api/papers/{paper_id}/bundle"))

    assert names == [
        "manifest.json",
        "paper.pdf",
        "metadata.json",
        "annotations.json",
        "sticky-notes.json",
        "summary-notes.json",
        "citation.bib",
    ]
    annotation = json.loads(members["annotations.json"])["annotations"][0]
    assert annotation["author"] == "assistant"
    assert annotation["remember"] is True
    assert paper_id not in members["annotations.json"].decode("utf-8")
    summary = json.loads(members["summary-notes.json"])["summary_notes"][0]
    assert summary["normalized_x"] is None
    assert paper_id not in members["summary-notes.json"].decode("utf-8")


def test_manifest_descriptors_match_all_extracted_members_and_no_extra_paths(
    service: CaeliaeReadService,
    uploaded: dict[str, object],
    session: dict[str, str],
) -> None:
    paper_id = uploaded["paper_id"]
    build_optional_records(service, paper_id, session["session_id"])
    service.replace_paper_metadata(paper_id, metadata_body(title="Manifest Study"))
    first = service.export_paper_bundle(paper_id, exported_at=FIXED_EXPORTED_AT)
    second = service.export_paper_bundle(paper_id, exported_at=FIXED_EXPORTED_AT)
    assert first.content == second.content

    with zipfile.ZipFile(BytesIO(first.content)) as archive:
        names = archive.namelist()
        manifest_bytes = archive.read("manifest.json")
        manifest = json.loads(manifest_bytes)
        assert names == [item["path"] for item in [{"path": "manifest.json"}] + manifest["files"]]
        for descriptor in manifest["files"]:
            data = archive.read(descriptor["path"])
            assert descriptor["size_bytes"] == len(data)
            assert descriptor["sha256"] == hashlib.sha256(data).hexdigest()
        assert set(names) == {"manifest.json", *(item["path"] for item in manifest["files"])}


def test_export_does_not_mutate_database_or_create_missing_metadata(
    service: CaeliaeReadService, uploaded: dict[str, object]
) -> None:
    paper_id = uploaded["paper_id"]
    before = database_snapshot(service)
    first = service.export_paper_bundle(paper_id, exported_at=FIXED_EXPORTED_AT)
    second = service.export_paper_bundle(paper_id, exported_at=FIXED_EXPORTED_AT)
    after = database_snapshot(service)

    assert first.content == second.content
    assert before == after
    assert service.repository.get_paper_metadata(paper_id) is None


@pytest.mark.parametrize("mutation", ["missing", "size", "sha", "pages"])
def test_storage_integrity_mismatch_fails_closed(
    client: TestClient,
    service: CaeliaeReadService,
    uploaded: dict[str, object],
    mutation: str,
) -> None:
    paper_id = uploaded["paper_id"]
    paper, path = service.get_paper_file(paper_id)
    if mutation == "missing":
        path.unlink()
    elif mutation == "size":
        path.write_bytes(b"%PDF-corrupted")
    elif mutation == "sha":
        with service.repository.connection() as connection:
            connection.execute("UPDATE papers SET content_sha256=? WHERE paper_id=?", ("0" * 64, paper_id))
            connection.commit()
    else:
        with service.repository.connection() as connection:
            connection.execute("UPDATE papers SET page_count=? WHERE paper_id=?", (paper.page_count + 1, paper_id))
            connection.commit()

    response = client.get(f"/api/papers/{paper_id}/bundle")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "paper_storage_integrity_error"
    assert str(service.settings.data_root) not in response.text


def test_unknown_paper_is_404_and_error_does_not_expose_storage_details(client: TestClient, service: CaeliaeReadService) -> None:
    response = client.get(f"/api/papers/{uuid4()}/bundle")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "paper_not_found"
    assert str(service.settings.data_root) not in response.text


def test_openapi_describes_zip_bundle_route(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/papers/{paper_id}/bundle"]["get"]
    assert operation["responses"]["200"]["content"]["application/zip"]["schema"] == {
        "type": "string",
        "format": "binary",
    }
