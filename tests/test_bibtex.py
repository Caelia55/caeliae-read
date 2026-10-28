from __future__ import annotations

import hashlib
import re

import pytest

from caeliae_read.bibtex import BibtexSerializationError, fallback_citation_key, serialize_bibtex
from caeliae_read.domain.models import Paper, PaperMetadata


def make_paper(paper_id: str = "paper-123") -> Paper:
    return Paper(
        paper_id=paper_id,
        original_filename="private-paper.pdf",
        size_bytes=123,
        page_count=4,
        created_at="2026-10-20T00:00:00Z",
    )


def make_metadata(**overrides: object) -> PaperMetadata:
    values: dict[str, object] = {
        "paper_id": "paper-123",
        "title": "A Study of Representation",
        "authors": ["Alice Smith", "Bob Chen"],
        "publication_year": 2026,
        "venue": "Example Journal",
        "doi": "10.1234/example",
        "arxiv_id": "2601.12345",
        "citation_key": "smith2026representation",
        "updated_at": "2026-10-20T00:00:00Z",
    }
    values.update(overrides)
    return PaperMetadata(**values)  # type: ignore[arg-type]


def structural_brace_balance(value: str) -> int:
    depth = 0
    escaped = False
    for character in value:
        if character == "{" and not escaped:
            depth += 1
        elif character == "}" and not escaped:
            depth -= 1
        escaped = character == "\\" and not escaped
        if character != "\\":
            escaped = False
    return depth


def top_level_at_signs(value: str) -> list[int]:
    depth = 0
    positions: list[int] = []
    escaped = False
    for index, character in enumerate(value):
        if character == "{" and not escaped:
            depth += 1
        elif character == "}" and not escaped:
            depth -= 1
        elif character == "@" and depth == 0:
            positions.append(index)
        escaped = character == "\\" and not escaped
        if character != "\\":
            escaped = False
    return positions


def test_complete_entry_has_fixed_order_and_exact_golden_shape() -> None:
    result = serialize_bibtex(make_paper(), make_metadata())

    assert result == (
        "@misc{smith2026representation,\n"
        "  title = {A Study of Representation},\n"
        "  author = {Alice Smith and Bob Chen},\n"
        "  year = {2026},\n"
        "  howpublished = {Example Journal},\n"
        "  doi = {10.1234/example},\n"
        "  eprint = {2601.12345},\n"
        "  archivePrefix = {arXiv}\n"
        "}\n"
    )


def test_title_is_required_and_other_optional_fields_are_omitted() -> None:
    with pytest.raises(BibtexSerializationError, match="metadata.title"):
        serialize_bibtex(make_paper(), make_metadata(title="  "))

    with pytest.raises(BibtexSerializationError, match="metadata.title"):
        serialize_bibtex(make_paper(), make_metadata(title=None))

    with pytest.raises(BibtexSerializationError, match="metadata.title"):
        serialize_bibtex(make_paper(), make_metadata(title="\n\t"))

    result = serialize_bibtex(
        make_paper(),
        make_metadata(
            authors=None,
            publication_year=None,
            venue=None,
            doi=None,
            arxiv_id=None,
        ),
    )
    assert result == (
        "@misc{smith2026representation,\n"
        "  title = {A Study of Representation}\n"
        "}\n"
    )
    assert "original_filename" not in result
    assert "paper-123" not in result
    assert "updated_at" not in result


def test_doi_and_arxiv_are_independent_and_arxiv_has_only_required_fields() -> None:
    doi_only = serialize_bibtex(make_paper(), make_metadata(arxiv_id=None))
    assert "  doi = {10.1234/example}\n" in doi_only
    assert "eprint" not in doi_only
    assert "archivePrefix" not in doi_only

    arxiv_only = serialize_bibtex(make_paper(), make_metadata(doi=None))
    assert "  eprint = {2601.12345},\n" in arxiv_only
    assert "  archivePrefix = {arXiv}\n" in arxiv_only
    assert "primaryClass" not in arxiv_only
    assert "https://" not in arxiv_only


def test_author_order_duplicates_and_literal_and_token_are_preserved() -> None:
    result = serialize_bibtex(
        make_paper(),
        make_metadata(
            authors=["Research and Development Group", "Alice Smith", "Alice Smith"]
        ),
    )
    assert "author = {{Research and Development Group} and Alice Smith and Alice Smith}" in result


def test_unicode_and_special_characters_are_utf8_escaped_without_injection() -> None:
    title = "研究 {模型} 100% & \"x\" # _ $ ^ ~ \\"
    result = serialize_bibtex(
        make_paper(),
        make_metadata(title=title, authors=["蔡佳颖", "A, B"], venue="Line 1\nLine 2\tend"),
    )

    assert result.encode("utf-8").decode("utf-8") == result
    assert not result.startswith("\ufeff")
    assert r"\{" in result and r"\}" in result
    assert r"\%" in result and r"\&" in result and r"\_" in result
    assert r"\$" in result and r"\^{}" in result and r"\~{}" in result
    assert r"\textbackslash{}" in result
    assert "Line 1 Line 2 end" in result

    malicious_title = "safe}\n@article{evil,author={attacker}}"
    malicious = serialize_bibtex(make_paper(), make_metadata(title=malicious_title))
    assert "\n@article" not in malicious
    assert len(top_level_at_signs(malicious)) == 1
    assert structural_brace_balance(malicious) == 0


def test_missing_citation_key_is_deterministic_temporary_and_safe() -> None:
    first = serialize_bibtex(make_paper("ABC/123 with spaces"), make_metadata(citation_key=None))
    second = serialize_bibtex(make_paper("ABC/123 with spaces"), make_metadata(citation_key=None))
    assert first == second
    assert first.startswith("@misc{caeliae-abc-123-with-spaces,")
    assert re.search(r"@misc\{[a-z0-9][a-z0-9._:-]*,", first)

    unusable_id = "///"
    expected_digest = hashlib.sha256(unusable_id.encode("utf-8")).hexdigest()
    assert fallback_citation_key(unusable_id) == f"caeliae-{expected_digest}"
    assert fallback_citation_key("x" * 500).startswith("caeliae-x")
    assert len(fallback_citation_key("x" * 500)) <= 128


def test_output_is_single_entry_with_balanced_structure_and_no_internal_metadata() -> None:
    result = serialize_bibtex(make_paper(), make_metadata())
    assert len(top_level_at_signs(result)) == 1
    assert structural_brace_balance(result) == 0
    assert result.endswith("}\n")
    assert not result.endswith("\n\n")
    assert "created_at" not in result
    assert "updated_at" not in result
