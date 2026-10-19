from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$")
ARXIV_NEW_PATTERN = re.compile(r"^\d{4}\.\d{4,5}$")
ARXIV_OLD_PATTERN = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z0-9-]+)?/\d{7}$")
CITATION_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class PaperMetadataNormalizationError(ValueError):
    pass


def _optional_text(value: Any, field: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PaperMetadataNormalizationError(f"{field} must be a string or null")
    value = value.strip()
    if not value:
        return None
    if len(value) > max_length:
        raise PaperMetadataNormalizationError(f"{field} exceeds {max_length} characters")
    return value


def normalize_doi(value: Any) -> str | None:
    result = _optional_text(value, "doi", max_length=512)
    if result is None:
        return None
    lowered = result.casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if lowered.startswith(prefix):
            result = result[len(prefix):].strip()
            break
    result = result.casefold()
    if not DOI_PATTERN.fullmatch(result):
        raise PaperMetadataNormalizationError("doi is not a valid DOI")
    return result


def normalize_arxiv_id(value: Any) -> str | None:
    result = _optional_text(value, "arxiv_id", max_length=128)
    if result is None:
        return None
    lowered = result.casefold()
    for prefix in ("https://arxiv.org/abs/", "http://arxiv.org/abs/", "arxiv:"):
        if lowered.startswith(prefix):
            result = result[len(prefix):].strip()
            break
    result = result.casefold()
    result = re.sub(r"v\d+$", "", result)
    if not (ARXIV_NEW_PATTERN.fullmatch(result) or ARXIV_OLD_PATTERN.fullmatch(result)):
        raise PaperMetadataNormalizationError("arxiv_id is not a supported arXiv identifier")
    return result


def normalize_paper_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    title = _optional_text(payload.get("title"), "title", max_length=2_000)

    raw_authors = payload.get("authors")
    authors: list[str] | None
    if raw_authors is None:
        authors = None
    elif not isinstance(raw_authors, list):
        raise PaperMetadataNormalizationError("authors must be an array of strings or null")
    else:
        if len(raw_authors) > 128:
            raise PaperMetadataNormalizationError("authors must contain at most 128 items")
        authors = []
        for author in raw_authors:
            if not isinstance(author, str):
                raise PaperMetadataNormalizationError("authors must contain only strings")
            normalized = author.strip()
            if normalized:
                if len(normalized) > 500:
                    raise PaperMetadataNormalizationError("each author must be at most 500 characters")
                authors.append(normalized)
        if not authors:
            authors = None

    year = payload.get("publication_year")
    if year is not None:
        if isinstance(year, bool) or not isinstance(year, int):
            raise PaperMetadataNormalizationError("publication_year must be an integer or null")
        if not 1000 <= year <= 2100:
            raise PaperMetadataNormalizationError("publication_year must be between 1000 and 2100")

    venue = _optional_text(payload.get("venue"), "venue", max_length=500)
    citation_key = _optional_text(payload.get("citation_key"), "citation_key", max_length=128)
    if citation_key is not None and not CITATION_KEY_PATTERN.fullmatch(citation_key):
        raise PaperMetadataNormalizationError("citation_key contains unsupported characters")

    return {
        "title": title,
        "authors": authors,
        "publication_year": year,
        "venue": venue,
        "doi": normalize_doi(payload.get("doi")),
        "arxiv_id": normalize_arxiv_id(payload.get("arxiv_id")),
        "citation_key": citation_key,
    }
