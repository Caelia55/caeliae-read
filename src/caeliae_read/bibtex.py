"""Pure BibTeX serialization for paper metadata.

This module deliberately has no service, API, database, filesystem, or network
dependencies.  It serializes already-loaded domain objects only.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from caeliae_read.domain.models import Paper, PaperMetadata

__all__ = [
    "BibtexSerializationError",
    "BibtexSerializationResult",
    "bibtex_attachment_filename",
    "fallback_citation_key",
    "portable_fallback_citation_key",
    "serialize_bibtex",
    "serialize_bibtex_result",
]


class BibtexSerializationError(ValueError):
    """Raised when a paper cannot be represented by the V1 BibTeX contract."""


@dataclass(frozen=True)
class BibtexSerializationResult:
    """Serialized text together with the exact entry key used in the body."""

    citation_key: str
    text: str


_MAX_CITATION_KEY_LENGTH = 128
_MAX_ATTACHMENT_KEY_LENGTH = 96
_SAFE_CITATION_KEY_RE = re.compile(r"[^a-z0-9._:-]+")
_SAFE_ATTACHMENT_KEY_RE = re.compile(r"[^A-Za-z0-9._-]+")
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


def portable_fallback_citation_key(content_sha256: str) -> str:
    """Return the bundle-only fallback key derived from PDF content identity."""

    if not isinstance(content_sha256, str) or re.fullmatch(r"[0-9a-fA-F]{64}", content_sha256) is None:
        raise BibtexSerializationError("content_sha256 must be a 64-character hexadecimal digest")
    return f"caeliae-{content_sha256.lower()[:16]}"


def bibtex_attachment_filename(citation_key: str) -> str:
    """Return a bounded ASCII-only attachment filename for a citation key."""

    if not isinstance(citation_key, str):
        citation_key = ""
    safe_key = _SAFE_ATTACHMENT_KEY_RE.sub("-", citation_key)
    safe_key = re.sub(r"-+", "-", safe_key).strip("._-")
    safe_key = safe_key[:_MAX_ATTACHMENT_KEY_LENGTH]
    if not safe_key:
        safe_key = "export"
    return f"caeliae-{safe_key}.bib"


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


def serialize_bibtex_result(
    paper: Paper,
    metadata: PaperMetadata,
    *,
    fallback_key: str | None = None,
) -> BibtexSerializationResult:
    """Serialize one paper and return the exact key used by the entry.

    ``fallback_key`` is an optional caller-supplied key for portable bundle
    exports. The standalone endpoint keeps its historical paper-ID fallback
    when this argument is omitted.
    """

    if not isinstance(metadata.title, str) or not _single_line(metadata.title):
        raise BibtexSerializationError("BibTeX export requires a non-empty metadata.title")

    citation_key = metadata.citation_key or fallback_key or fallback_citation_key(paper.paper_id)
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
    return BibtexSerializationResult(citation_key=citation_key, text="\n".join(lines) + "\n")


def serialize_bibtex(
    paper: Paper,
    metadata: PaperMetadata,
    *,
    fallback_key: str | None = None,
) -> str:
    """Serialize one paper as exactly one deterministic ``@misc`` entry."""

    return serialize_bibtex_result(paper, metadata, fallback_key=fallback_key).text
