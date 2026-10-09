from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient


def upload(client: TestClient, filename: str, body: bytes, mime: str = "application/pdf"):
    return client.post("/api/papers", files={"file": (filename, body, mime)})


def test_upload_is_streamed_validated_and_public_metadata_is_safe(
    client: TestClient, fixture_pdf: Path
) -> None:
    body = fixture_pdf.read_bytes()
    response = upload(client, "paper.pdf", body)
    assert response.status_code == 201
    result = response.json()
    paper = result["paper"]
    UUID(paper["paper_id"])
    assert paper["paper_id"] != hashlib.sha256(body).hexdigest()
    assert paper["page_count"] == 2
    assert set(paper) == {
        "paper_id", "original_filename", "size_bytes", "page_count", "created_at"
    }
    serialized = response.text.lower()
    assert "storage_key" not in serialized
    assert "content_sha256" not in serialized
    assert "\\users\\" not in serialized
    assert ":\\" not in serialized


def test_same_content_is_deduplicated(client: TestClient, fixture_pdf: Path) -> None:
    body = fixture_pdf.read_bytes()
    first = upload(client, "first.pdf", body).json()
    second = upload(client, "renamed.pdf", body).json()
    assert first["deduplicated"] is False
    assert second["deduplicated"] is True
    assert first["paper"]["paper_id"] == second["paper"]["paper_id"]
    assert client.get("/api/papers").json()["papers"] == [first["paper"]]


def test_upload_rejects_mime_magic_and_path_names(
    client: TestClient, fixture_pdf: Path
) -> None:
    body = fixture_pdf.read_bytes()
    assert upload(client, "paper.pdf", body, "text/plain").status_code == 400
    assert upload(client, "paper.pdf", b"not a pdf").status_code == 400
    assert upload(client, "../paper.pdf", body).status_code == 400
    assert upload(client, "folder\\paper.pdf", body).status_code == 400


def test_upload_does_not_require_a_filename_extension(
    client: TestClient, fixture_pdf: Path
) -> None:
    response = upload(client, "paper-from-browser", fixture_pdf.read_bytes())
    assert response.status_code == 201
    assert response.json()["paper"]["original_filename"] == "paper-from-browser"


def test_upload_enforces_size_limit(client: TestClient) -> None:
    response = upload(client, "large.pdf", b"%PDF-" + b"x" * 1_000_001)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "validation_error"
