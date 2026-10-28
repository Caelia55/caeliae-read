from __future__ import annotations

import socket
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from caeliae_read.bibtex import serialize_bibtex
from caeliae_read.application.service import CaeliaeReadService


def metadata_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "title": "A BibTeX Paper",
        "authors": ["Alice Smith", "Bob Chen"],
        "publication_year": 2026,
        "venue": "Example Journal",
        "doi": "https://doi.org/10.1234/example",
        "arxiv_id": "https://arxiv.org/abs/2601.12345v2",
        "citation_key": "Smith:2026",
    }
    body.update(overrides)
    return body


def save_metadata(
    client: TestClient, paper_id: str, **overrides: object
) -> dict[str, object]:
    response = client.put(
        f"/api/papers/{paper_id}/metadata", json=metadata_body(**overrides)
    )
    assert response.status_code == 200
    return response.json()["metadata"]


def test_complete_export_matches_serializer_and_uses_safe_attachment_headers(
    client: TestClient,
    service: CaeliaeReadService,
    uploaded: dict[str, object],
) -> None:
    paper_id = str(uploaded["paper_id"])
    save_metadata(client, paper_id)

    metadata = service.repository.get_paper_metadata(paper_id)
    assert metadata is not None
    expected = serialize_bibtex(service.get_paper(paper_id), metadata)

    response = client.get(f"/api/papers/{paper_id}/bibtex")

    assert response.status_code == 200
    assert response.content.decode("utf-8") == expected
    assert response.headers["content-type"].split(";", 1)[0] == "application/x-bibtex"
    assert "charset=utf-8" in response.headers["content-type"].lower()
    assert response.headers["content-disposition"] == (
        'attachment; filename="caeliae-Smith-2026.bib"'
    )


def test_colon_stays_in_entry_key_but_is_sanitized_in_filename(
    client: TestClient, uploaded: dict[str, object]
) -> None:
    paper_id = str(uploaded["paper_id"])
    save_metadata(client, paper_id, citation_key="Alice:2026.Example_Key")

    response = client.get(f"/api/papers/{paper_id}/bibtex")

    assert response.status_code == 200
    assert response.text.startswith("@misc{Alice:2026.Example_Key,")
    assert response.headers["content-disposition"] == (
        'attachment; filename="caeliae-Alice-2026.Example_Key.bib"'
    )


def test_missing_citation_key_has_stable_fallback_and_does_not_persist(
    client: TestClient, uploaded: dict[str, object]
) -> None:
    paper_id = str(uploaded["paper_id"])
    save_metadata(client, paper_id, citation_key=None)
    before = client.get(f"/api/papers/{paper_id}/metadata").json()

    first = client.get(f"/api/papers/{paper_id}/bibtex")
    second = client.get(f"/api/papers/{paper_id}/bibtex")
    after = client.get(f"/api/papers/{paper_id}/metadata").json()

    fallback = f"caeliae-{paper_id.casefold()}"
    assert first.status_code == 200
    assert first.content == second.content
    assert first.text.startswith(f"@misc{{{fallback},")
    assert first.headers["content-disposition"] == (
        f'attachment; filename="caeliae-{fallback}.bib"'
    )
    assert before == after
    assert after["metadata"]["citation_key"] is None


def test_export_keeps_optional_metadata_omitted_and_supports_doi_only(
    client: TestClient, uploaded: dict[str, object]
) -> None:
    paper_id = str(uploaded["paper_id"])
    save_metadata(
        client,
        paper_id,
        authors=None,
        publication_year=None,
        venue=None,
        doi="10.1234/doi-only",
        arxiv_id=None,
        citation_key="doi2026",
    )

    response = client.get(f"/api/papers/{paper_id}/bibtex")

    assert response.status_code == 200
    assert "  title = {A BibTeX Paper},\n" in response.text
    assert "  doi = {10.1234/doi-only}\n" in response.text
    assert "author" not in response.text
    assert "year" not in response.text
    assert "howpublished" not in response.text
    assert "eprint" not in response.text


def test_arxiv_unicode_and_body_format_are_preserved(
    client: TestClient, uploaded: dict[str, object]
) -> None:
    paper_id = str(uploaded["paper_id"])
    save_metadata(
        client,
        paper_id,
        title="研究论文",
        authors=["蔡佳颖"],
        doi=None,
        arxiv_id="2601.12345",
        citation_key="unicode2026",
    )

    response = client.get(f"/api/papers/{paper_id}/bibtex")

    assert response.status_code == 200
    assert response.content.decode("utf-8") == response.text
    assert "  title = {研究论文},\n" in response.text
    assert "  author = {蔡佳颖},\n" in response.text
    assert "  eprint = {2601.12345},\n" in response.text
    assert "  archivePrefix = {arXiv}\n" in response.text
    assert response.text.endswith("}\n")
    assert not response.text.endswith("\n\n")


def test_missing_metadata_and_incomplete_metadata_use_stable_422_errors(
    client: TestClient, uploaded: dict[str, object]
) -> None:
    paper_id = str(uploaded["paper_id"])
    missing = client.get(f"/api/papers/{paper_id}/bibtex")
    assert missing.status_code == 422
    assert missing.json()["detail"] == {
        "code": "paper_metadata_not_found",
        "message": "paper metadata is required for BibTeX export",
    }

    all_null = client.put(
        f"/api/papers/{paper_id}/metadata",
        json=metadata_body(
            title=None,
            authors=None,
            publication_year=None,
            venue=None,
            doi=None,
            arxiv_id=None,
            citation_key=None,
        ),
    )
    assert all_null.status_code == 200
    incomplete = client.get(f"/api/papers/{paper_id}/bibtex")
    assert incomplete.status_code == 422
    assert incomplete.json()["detail"] == {
        "code": "paper_metadata_incomplete",
        "message": "BibTeX export requires a bibliographic title.",
    }


def test_title_null_and_missing_paper_have_expected_errors(
    client: TestClient, uploaded: dict[str, object]
) -> None:
    paper_id = str(uploaded["paper_id"])
    saved = save_metadata(client, paper_id, title="   ", authors=["Alice Smith"])
    assert saved["title"] is None
    response = client.get(f"/api/papers/{paper_id}/bibtex")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "paper_metadata_incomplete"

    missing = client.get(f"/api/papers/{uuid4()}/bibtex")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "paper_not_found"


def test_openapi_describes_plain_bibtex_response(
    client: TestClient, uploaded: dict[str, object]
) -> None:
    schema = client.get("/openapi.json").json()
    response = schema["paths"]["/api/papers/{paper_id}/bibtex"]["get"]["responses"]["200"]
    assert response["content"]["application/x-bibtex"]["schema"] == {"type": "string"}


def test_export_does_not_require_network(
    client: TestClient,
    uploaded: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_metadata(client, str(uploaded["paper_id"]))

    def fail_network(*_: object, **__: object) -> object:
        raise AssertionError("BibTeX export attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    response = client.get(f"/api/papers/{uploaded['paper_id']}/bibtex")
    assert response.status_code == 200
