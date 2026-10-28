"""Pure BibTeX serialization for paper metadata.

This module deliberately has no service, API, database, filesystem, or network
dependencies.  It serializes already-loaded domain objects only.
"""

from __future__ import annotations

import hashlib
import re

from caeliae_read.domain.models import Paper, PaperMetadata

__all__ = [
    "BibtexSerializationError",
    "fallback_citation_key",
    "serialize_bibtex",
]


class BibtexSerializationError(ValueError):
    """Raised when a paper cannot be represented by the V1 BibTeX contract."""


_MAX_CITATION_KEY_LENGTH = 128
_SAFE_CITATION_KEY_RE = re.compile(r"[^a-z0-9._:-]+")
_BIBTEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "%": r"\%",
    "#": r"\#",
    "&": r"\&",
    "_": r"\_",
    "$": r"\$",
    "^": r"\^{}",
    "~": r"\~{}",
}


def _single_line(value: str) -> str:
    """Make a metadata value safe to emit on one BibTeX source line.

    Only layout whitespace is replaced.  Existing ordinary internal spaces are
    preserved rather than being collapsed.
    """

    return value.strip().replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")


def _escape_value(value: str) -> str:
    return "".join(_BIBTEX_ESCAPES.get(character, character) for character in _single_line(value))


def fallback_citation_key(paper_id: str) -> str:
    """Return a deterministic, temporary BibTeX-safe key for a paper.

    The key is never persisted.  It is derived from a normalized paper ID when
    possible, and falls back to a digest when the ID has no usable characters.
    """

    if not isinstance(paper_id, str):
        raise TypeError("paper_id must be a string")

    normalized = paper_id.casefold()
    sanitized = _SAFE_CITATION_KEY_RE.sub("-", normalized)
    sanitized = sanitized.strip("._:-")
    sanitized = sanitized.lstrip("-._:")
    if sanitized:
        return f"caeliae-{sanitized}"[:_MAX_CITATION_KEY_LENGTH]

    digest = hashlib.sha256(paper_id.encode("utf-8")).hexdigest()
    return f"caeliae-{digest}"[:_MAX_CITATION_KEY_LENGTH]


def _author_value(authors: list[str]) -> str | None:
    rendered: list[str] = []
    for author in authors:
        if not isinstance(author, str):
            raise BibtexSerializationError("metadata authors must be strings")
        normalized = _single_line(author)
        if not normalized:
            continue
        escaped = _escape_value(normalized)
        # Preserve an individual display name containing BibTeX's separator
        # token as one logical author.  No surname parsing or name rewriting.
        if " and " in normalized:
            escaped = "{" + escaped + "}"
        rendered.append(escaped)
    return " and ".join(rendered) or None


def serialize_bibtex(paper: Paper, metadata: PaperMetadata) -> str:
    """Serialize one paper as exactly one deterministic ``@misc`` entry."""

    if not isinstance(metadata.title, str) or not _single_line(metadata.title):
        raise BibtexSerializationError("BibTeX export requires a non-empty metadata.title")

    citation_key = metadata.citation_key or fallback_citation_key(paper.paper_id)
    fields: list[tuple[str, str]] = [("title", _escape_value(metadata.title))]

    if metadata.authors:
        author = _author_value(metadata.authors)
        if author is not None:
            fields.append(("author", author))
    if metadata.publication_year is not None:
        fields.append(("year", _escape_value(str(metadata.publication_year))))
    if metadata.venue:
        venue = _single_line(metadata.venue)
        if venue:
            fields.append(("howpublished", _escape_value(venue)))
    if metadata.doi:
        doi = _single_line(metadata.doi)
        if doi:
            fields.append(("doi", _escape_value(doi)))
    if metadata.arxiv_id:
        arxiv_id = _single_line(metadata.arxiv_id)
        if arxiv_id:
            fields.append(("eprint", _escape_value(arxiv_id)))
            fields.append(("archivePrefix", "arXiv"))

    lines = [f"@misc{{{citation_key},"]
    last_index = len(fields) - 1
    for index, (name, value) in enumerate(fields):
        comma = "," if index != last_index else ""
        lines.append(f"  {name} = {{{value}}}{comma}")
    lines.append("}")
    return "\n".join(lines) + "\n"
