from __future__ import annotations

from dataclasses import replace
from io import BytesIO
import hashlib
import json
from pathlib import Path
import zipfile
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from caeliae_read.application.bundle_export import assemble_paper_bundle
from caeliae_read.application.service import CaeliaeReadService
from caeliae_read.bundle import sha256_bytes
from caeliae_read.bundle_import import BundleValidationError, parse_paper_bundle
from caeliae_read.domain.models import Annotation, Paper, PaperMetadata, StickyNote, SummaryNote


FIXED_EXPORTED_AT = "2026-10-29T00:00:00Z"


def metadata(**overrides: object) -> PaperMetadata:
    values: dict[str, object] = {
        "title": None,
        "authors": None,
        "publication_year": None,
        "venue": None,
        "doi": None,
        "arxiv_id": None,
        "citation_key": None,
    }
    values.update(overrides)
    return PaperMetadata(
        paper_id="00000000-0000-0000-0000-000000000001",
        updated_at="2026-10-29T00:00:00Z",
        **values,
    )


def assemble_for_pdf(pdf: bytes, filename: str = "research-paper.pdf", metadata_value: PaperMetadata | None = None, *, annotations: tuple[Annotation, ...] = (), sticky_notes: tuple[StickyNote, ...] = (), summary_notes: tuple[SummaryNote, ...] = ()) -> bytes:
    page_count = len(PdfReader(BytesIO(pdf), strict=False).pages)
    paper = Paper("00000000-0000-0000-0000-000000000002", filename, len(pdf), page_count, "2026-10-29T00:00:00Z")
    return assemble_paper_bundle(
        paper=paper,
        content_sha256=sha256_bytes(pdf),
        mime_type="application/pdf",
        pdf_bytes=pdf,
        metadata=metadata_value,
        annotations=annotations,
        sticky_notes=sticky_notes,
        summary_notes=summary_notes,
        exported_at=FIXED_EXPORTED_AT,
    ).content


def archive_members(bundle: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(BytesIO(bundle)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def rebuild_archive(members: dict[str, bytes], *, duplicate: str | None = None) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
        if duplicate is not None:
            archive.writestr(duplicate, members[duplicate])
    return output.getvalue()


def update_manifest_for_member(members: dict[str, bytes], path: str, data: bytes) -> dict[str, bytes]:
    updated = dict(members)
    updated[path] = data
    manifest = json.loads(updated["manifest.json"])
    for descriptor in manifest["files"]:
        if descriptor["path"] == path:
            descriptor["size_bytes"] = len(data)
            descriptor["sha256"] = hashlib.sha256(data).hexdigest()
            break
    else:
        raise AssertionError(path)
    updated["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return updated


def database_snapshot(service: CaeliaeReadService) -> dict[str, bytes | None]:
    path = service.repository.database_path
    return {
        "db": path.read_bytes() if path.exists() else None,
        "wal": path.with_name(f"{path.name}-wal").read_bytes() if path.with_name(f"{path.name}-wal").exists() else None,
        "shm": path.with_name(f"{path.name}-shm").read_bytes() if path.with_name(f"{path.name}-shm").exists() else None,
    }


def preview(client: TestClient, bundle: bytes):
    return client.post("/api/paper-bundles/preview", files={"bundle": ("preview.zip", bundle, "application/zip")})


def test_valid_preview_is_existing_and_zero_write(client: TestClient, service: CaeliaeReadService, uploaded: dict[str, object]) -> None:
    bundle = client.get(f"/api/papers/{uploaded['paper_id']}/bundle").content
    before = database_snapshot(service)
    first = preview(client, bundle)
    second = preview(client, bundle)
    after = database_snapshot(service)

    assert first.status_code == 200
    assert first.json() == second.json()
    body = first.json()
    assert body["valid"] is True
    assert body["target"]["paper_status"] == "existing"
    assert body["target"]["paper_id"] == uploaded["paper_id"]
    assert body["target"]["metadata"]["status"] == "incoming_absent"
    assert body["can_import"] is True
    assert before == after


def test_new_and_filename_identity_are_hash_anchored(client: TestClient, fixture_pdf: Path, uploaded: dict[str, object]) -> None:
    alternate_pdf = fixture_pdf.read_bytes() + b"\n% alternate content identity\n"
    new_bundle = assemble_for_pdf(alternate_pdf, "research-paper.pdf")
    new_preview = preview(client, new_bundle)
    assert new_preview.status_code == 200
    assert new_preview.json()["target"]["paper_status"] == "new"
    assert new_preview.json()["target"]["paper_id"] is None

    same_hash_members = archive_members(client.get(f"/api/papers/{uploaded['paper_id']}/bundle").content)
    manifest = json.loads(same_hash_members["manifest.json"])
    manifest["paper"]["original_filename"] = "renamed-paper.pdf"
    same_hash_members["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    existing_preview = preview(client, rebuild_archive(same_hash_members))
    assert existing_preview.status_code == 200
    assert existing_preview.json()["target"]["paper_status"] == "existing"


def test_metadata_collision_statuses(client: TestClient, fixture_pdf: Path, uploaded: dict[str, object]) -> None:
    present_bundle = assemble_for_pdf(fixture_pdf.read_bytes(), metadata_value=metadata(title="Incoming title"))
    would_create = preview(client, present_bundle).json()
    assert would_create["target"]["metadata"]["status"] == "would_create"

    paper_id = uploaded["paper_id"]
    assert client.put(f"/api/papers/{paper_id}/metadata", json={
        "title": "Incoming title",
        "authors": None,
        "publication_year": None,
        "venue": None,
        "doi": None,
        "arxiv_id": None,
        "citation_key": None,
    }).status_code == 200
    identical = preview(client, present_bundle).json()
    assert identical["target"]["metadata"]["status"] == "identical"

    conflict_bundle = assemble_for_pdf(fixture_pdf.read_bytes(), metadata_value=metadata(title="Different title"))
    conflict = preview(client, conflict_bundle).json()
    assert conflict["target"]["metadata"]["status"] == "conflict"
    assert conflict["target"]["metadata"]["differences"] == [{
        "field": "title", "current": "Incoming title", "incoming": "Different title",
    }]
    assert conflict["can_import"] is False
    assert "metadata" in conflict["blocking_conflicts"]


def test_present_all_null_metadata_is_distinct_from_absent(client: TestClient, fixture_pdf: Path, uploaded: dict[str, object]) -> None:
    bundle = assemble_for_pdf(fixture_pdf.read_bytes(), metadata_value=metadata())
    result = preview(client, bundle).json()
    assert result["target"]["metadata"]["status"] == "would_create"
    assert result["can_import"] is True


def test_optional_records_identical_and_conflicting(client: TestClient, service: CaeliaeReadService, fixture_pdf: Path, uploaded: dict[str, object], session: dict[str, str]) -> None:
    paper_id = uploaded["paper_id"]
    annotation = Annotation(
        annotation_id=str(uuid4()), thread_id=str(uuid4()), paper_id=paper_id, session_id=session["session_id"],
        page_number=1, exact_text="Selected passage", prefix="", suffix="", normalized_quads=[{"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.04}],
        page_width=612, page_height=792, rotation=0, author="assistant", note="assistant note", remember=True,
        style_key=None, mark_type="underline", created_at=FIXED_EXPORTED_AT, updated_at=FIXED_EXPORTED_AT,
    )
    service.repository.save_annotation(
        annotation=annotation.public_dict(), idempotency_key=str(uuid4()), payload_sha256=sha256_bytes(b"annotation"),
        response_json=json.dumps({"annotation": annotation.public_dict()}), created_at=FIXED_EXPORTED_AT, author="assistant",
    )
    sticky = StickyNote(str(uuid4()), paper_id, 1, 0.4, 0.5, "sticky", "secondary", FIXED_EXPORTED_AT, FIXED_EXPORTED_AT)
    service.repository.insert_sticky_note({"id": sticky.id, "paper_id": paper_id, "page": sticky.page, "x": sticky.x, "y": sticky.y, "text": sticky.text, "style_key": sticky.style_key}, FIXED_EXPORTED_AT)
    summary = SummaryNote(str(uuid4()), paper_id, 1, 0.6, None, "summary", FIXED_EXPORTED_AT, FIXED_EXPORTED_AT)
    service.repository.insert_summary_note({"summary_note_id": summary.summary_note_id, "paper_id": paper_id, "page_number": 1, "normalized_y": summary.normalized_y, "normalized_x": summary.normalized_x, "text": summary.text}, FIXED_EXPORTED_AT)

    exported = service.export_paper_bundle(paper_id, exported_at=FIXED_EXPORTED_AT).content
    identical = preview(client, exported).json()
    assert identical["target"]["annotations"]["identical"] == 1
    assert identical["target"]["sticky_notes"]["identical"] == 1
    assert identical["target"]["summary_notes"]["identical"] == 1
    assert identical["can_import"] is True

    changed = replace(annotation, note="changed note")
    conflict_bundle = assemble_for_pdf(
        fixture_pdf.read_bytes(),
        annotations=(changed,),
        sticky_notes=(sticky,),
        summary_notes=(summary,),
    )
    conflict = preview(client, conflict_bundle).json()
    assert conflict["target"]["annotations"]["conflict"] == 1
    assert conflict["can_import"] is False


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("missing_manifest", "bundle_schema_error"),
        ("missing_paper", "bundle_schema_error"),
        ("missing_metadata", "bundle_schema_error"),
        ("unexpected_file", "unsafe_bundle_archive"),
        ("traversal", "unsafe_bundle_archive"),
        ("duplicate_member", "unsafe_bundle_archive"),
        ("bad_format", "invalid_bundle"),
        ("bad_major", "unsupported_bundle_version"),
        ("bad_json", "bundle_schema_error"),
        ("duplicate_json_key", "bundle_schema_error"),
        ("invalid_utf8", "bundle_schema_error"),
        ("wrong_checksum", "bundle_integrity_error"),
    ],
)
def test_validation_failures_are_stable_and_zero_write(client: TestClient, service: CaeliaeReadService, fixture_pdf: Path, uploaded: dict[str, object], mutation: str, code: str) -> None:
    valid = client.get(f"/api/papers/{uploaded['paper_id']}/bundle").content
    members = archive_members(valid)
    if mutation == "missing_manifest":
        members.pop("manifest.json")
    elif mutation == "missing_paper":
        members.pop("paper.pdf")
    elif mutation == "missing_metadata":
        members.pop("metadata.json")
    elif mutation == "unexpected_file":
        members["unexpected.bin"] = b"unexpected"
    elif mutation == "traversal":
        members["../escape"] = b"escape"
    elif mutation == "duplicate_member":
        malformed = rebuild_archive(members, duplicate="paper.pdf")
        before = database_snapshot(service)
        response = preview(client, malformed)
        assert response.status_code == 422 and response.json()["detail"]["code"] == code
        assert database_snapshot(service) == before
        return
    elif mutation == "bad_format":
        manifest = json.loads(members["manifest.json"])
        manifest["format"] = "other-format"
        members["manifest.json"] = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    elif mutation == "bad_major":
        manifest = json.loads(members["manifest.json"])
        manifest["version"] = "2.0"
        members["manifest.json"] = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    elif mutation == "bad_json":
        members["manifest.json"] = b"{"
    elif mutation == "duplicate_json_key":
        members["manifest.json"] = b'{"format":"caeliae-paper-bundle","format":"caeliae-paper-bundle"}'
    elif mutation == "invalid_utf8":
        members["manifest.json"] = b"\xff\xfe"
    elif mutation == "wrong_checksum":
        members["paper.pdf"] = fixture_pdf.read_bytes() + b"tampered"
    malformed = rebuild_archive(members)
    before = database_snapshot(service)
    response = preview(client, malformed)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == code
    assert database_snapshot(service) == before


def test_summary_null_x_is_valid_and_invalid_x_is_rejected(fixture_pdf: Path) -> None:
    note = SummaryNote(str(uuid4()), "paper", 1, 0.2, None, "legacy", FIXED_EXPORTED_AT, FIXED_EXPORTED_AT)
    bundle = assemble_for_pdf(fixture_pdf.read_bytes(), summary_notes=(note,))
    parsed = parse_paper_bundle(bundle)
    assert parsed.summary_notes[0]["normalized_x"] is None

    members = archive_members(bundle)
    summary = json.loads(members["summary-notes.json"])
    summary["summary_notes"][0]["normalized_x"] = 2
    changed = (json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n").encode()
    members = update_manifest_for_member(members, "summary-notes.json", changed)
    with pytest.raises(BundleValidationError) as error:
        parse_paper_bundle(rebuild_archive(members))
    assert getattr(error.value, "code", None) == "bundle_schema_error"
