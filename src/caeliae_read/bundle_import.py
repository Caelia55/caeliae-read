"""Safe, read-only parsing for Portable Paper Bundle V1 archives.

This module validates an archive before any target database lookup.  It does
not write files, create papers, or depend on a repository.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from collections.abc import Mapping
from io import BytesIO
import re
import stat
from typing import Any
from uuid import UUID
import zipfile

from pypdf import PdfReader

from caeliae_read.bibtex import BibtexSerializationError, portable_fallback_citation_key, serialize_bibtex_result
from caeliae_read.bundle import (
    ANNOTATIONS_SCHEMA,
    BUNDLE_FORMAT,
    BUNDLE_VERSION,
    BundleSerializationError,
    CITATION_FILE_KIND,
    METADATA_SCHEMA,
    PaperIdentity,
    STICKY_NOTES_SCHEMA,
    SUMMARY_NOTES_SCHEMA,
    sha256_bytes,
)
from caeliae_read.domain.models import Paper, PaperMetadata


MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 16
MAX_PDF_BYTES = 200 * 1024 * 1024
MAX_JSON_BYTES = 20 * 1024 * 1024
MAX_CITATION_BYTES = 2 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")
_FILE_SPECS: dict[str, tuple[str, bool]] = {
    "paper.pdf": ("paper", True),
    "metadata.json": ("metadata", True),
    "annotations.json": ("annotations", False),
    "sticky-notes.json": ("sticky-notes", False),
    "summary-notes.json": ("summary-notes", False),
    "citation.bib": (CITATION_FILE_KIND, False),
}
_FILE_ORDER = tuple(_FILE_SPECS)
_METADATA_FIELDS = (
    "title",
    "authors",
    "publication_year",
    "venue",
    "doi",
    "arxiv_id",
    "citation_key",
)
_ANNOTATION_FIELDS = (
    "annotation_id",
    "thread_id",
    "page_number",
    "exact_text",
    "prefix",
    "suffix",
    "normalized_quads",
    "page_width",
    "page_height",
    "rotation",
    "author",
    "note",
    "remember",
    "style_key",
    "mark_type",
    "created_at",
    "updated_at",
)
_STICKY_FIELDS = ("id", "page_number", "normalized_x", "normalized_y", "text", "style_key", "created_at", "updated_at")
_SUMMARY_FIELDS = ("summary_note_id", "page_number", "normalized_y", "normalized_x", "text", "created_at", "updated_at")
_AUTHORS = {"user", "assistant"}
_MARK_TYPES = {"underline", "highlight"}
_STYLE_KEYS = {"primary", "secondary", "tertiary"}


class BundleValidationError(ValueError):
    """A public, stable validation failure for a bundle preview request."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ParsedPortableBundle:
    manifest: dict[str, Any]
    paper: dict[str, Any]
    paper_pdf: bytes
    metadata: dict[str, Any]
    annotations: tuple[dict[str, Any], ...]
    sticky_notes: tuple[dict[str, Any], ...]
    summary_notes: tuple[dict[str, Any], ...]
    citation_bibtex: str


def _fail(code: str, message: str) -> None:
    raise BundleValidationError(code, message)


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        _fail(code, message)


def _keys(value: object, expected: set[str], field: str) -> dict[str, Any]:
    _require(isinstance(value, dict), "bundle_schema_error", f"{field} must be an object")
    result = value
    if set(result) != expected:
        _fail("bundle_schema_error", f"{field} has an invalid field set")
    return result


def _string(value: object, field: str, *, allow_empty: bool = False, max_length: int | None = None) -> str:
    _require(isinstance(value, str), "bundle_schema_error", f"{field} must be a string")
    if not allow_empty and not value:
        _fail("bundle_schema_error", f"{field} must not be empty")
    if max_length is not None and len(value) > max_length:
        _fail("bundle_schema_error", f"{field} exceeds its size limit")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        _fail("bundle_schema_error", f"{field} must be valid UTF-8")
    return value


def _optional_string(value: object, field: str, *, max_length: int | None = None) -> str | None:
    if value is None:
        return None
    return _string(value, field, allow_empty=True, max_length=max_length)


def _integer(value: object, field: str, *, minimum: int | None = None) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool), "bundle_schema_error", f"{field} must be an integer")
    if minimum is not None and value < minimum:
        _fail("bundle_schema_error", f"{field} is out of range")
    return value


def _number(value: object, field: str, *, minimum: float | None = None, maximum: float | None = None, strict_positive: bool = False) -> float | int:
    _require(isinstance(value, (int, float)) and not isinstance(value, bool), "bundle_schema_error", f"{field} must be a number")
    numeric = float(value)
    _require(math.isfinite(numeric), "bundle_schema_error", f"{field} must be finite")
    if strict_positive and numeric <= 0:
        _fail("bundle_schema_error", f"{field} must be positive")
    if minimum is not None and numeric < minimum:
        _fail("bundle_schema_error", f"{field} is out of range")
    if maximum is not None and numeric > maximum:
        _fail("bundle_schema_error", f"{field} is out of range")
    return value


def _uuid(value: object, field: str) -> str:
    _require(isinstance(value, str), "bundle_schema_error", f"{field} must be a UUID")
    try:
        return str(UUID(value))
    except (TypeError, ValueError):
        _fail("bundle_schema_error", f"{field} must be a UUID")


def _sha256(value: object, field: str) -> str:
    result = _string(value, field)
    _require(_SHA256_RE.fullmatch(result) is not None, "bundle_schema_error", f"{field} must be lowercase SHA-256")
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(value)


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _parse_json(data: bytes, member: str) -> object:
    try:
        text = data.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as error:
        raise BundleValidationError("bundle_schema_error", f"{member} is not valid JSON") from error


def _validate_archive_name(name: str) -> None:
    _require(bool(name), "unsafe_bundle_archive", "archive contains an empty member name")
    _require("\x00" not in name, "unsafe_bundle_archive", "archive contains a NUL member name")
    _require("\\" not in name, "unsafe_bundle_archive", "archive member paths must use canonical separators")
    _require(not name.startswith("/"), "unsafe_bundle_archive", "archive contains an absolute member path")
    _require(re.match(r"^[A-Za-z]:", name) is None, "unsafe_bundle_archive", "archive contains a drive-qualified member path")
    parts = name.split("/")
    _require(".." not in parts and "." not in parts, "unsafe_bundle_archive", "archive contains a traversal member path")
    _require(all(parts), "unsafe_bundle_archive", "archive contains an invalid member path")


def _validate_zip_info(info: zipfile.ZipInfo) -> None:
    _validate_archive_name(info.filename)
    _require(not info.is_dir() and not info.filename.endswith("/"), "unsafe_bundle_archive", "directories are not allowed in a V1 bundle")
    _require((info.flag_bits & 0x1) == 0, "unsafe_bundle_archive", "encrypted ZIP members are not supported")
    _require(info.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}, "unsafe_bundle_archive", "ZIP compression method is not supported")
    mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    _require(file_type in {0, stat.S_IFREG}, "unsafe_bundle_archive", "non-regular ZIP members are not supported")
    if info.file_size > 1024:
        compressed = max(info.compress_size, 1)
        _require(info.file_size / compressed <= MAX_COMPRESSION_RATIO, "unsafe_bundle_archive", "ZIP compression ratio exceeds the V1 limit")


def _member_limit(name: str) -> int:
    if name == "paper.pdf":
        return MAX_PDF_BYTES
    if name == "citation.bib":
        return MAX_CITATION_BYTES
    return MAX_JSON_BYTES


def _validate_version(value: object) -> None:
    version = _string(value, "version")
    match = _VERSION_RE.fullmatch(version)
    _require(match is not None, "unsupported_bundle_version", "bundle version is unsupported")
    major, minor = int(match.group(1)), int(match.group(2))
    if major != 1 or minor != 0:
        _fail("unsupported_bundle_version", "bundle version is unsupported")


def _validate_manifest(value: object) -> tuple[dict[str, Any], dict[str, Any], dict[str, tuple[str, bool, int, str]]]:
    manifest = _keys(value, {"format", "version", "exported_at", "paper", "files"}, "manifest")
    _require(manifest["format"] == BUNDLE_FORMAT, "invalid_bundle", "bundle format is unsupported")
    _validate_version(manifest["version"])
    _string(manifest["exported_at"], "exported_at")

    paper = _keys(manifest["paper"], {"content_sha256", "mime_type", "size_bytes", "page_count", "original_filename"}, "manifest.paper")
    try:
        identity = PaperIdentity(
            content_sha256=_sha256(paper["content_sha256"], "paper.content_sha256"),
            mime_type=_string(paper["mime_type"], "paper.mime_type"),
            size_bytes=_integer(paper["size_bytes"], "paper.size_bytes", minimum=1),
            page_count=_integer(paper["page_count"], "paper.page_count", minimum=1),
            original_filename=_string(paper["original_filename"], "paper.original_filename"),
        )
    except (BundleSerializationError, ValueError) as error:
        raise BundleValidationError("bundle_schema_error", "manifest.paper is invalid") from error
    paper_payload = identity.to_dict()

    raw_files = manifest["files"]
    _require(isinstance(raw_files, list), "bundle_schema_error", "manifest.files must be an array")
    descriptors: dict[str, tuple[str, bool, int, str]] = {}
    for item in raw_files:
        descriptor = _keys(item, {"path", "kind", "required", "size_bytes", "sha256"}, "manifest.files[]")
        path = _string(descriptor["path"], "file.path")
        _require(path in _FILE_SPECS, "bundle_schema_error", "manifest contains an unsupported file path")
        _require(path not in descriptors, "bundle_schema_error", "manifest contains duplicate file paths")
        kind, required = _FILE_SPECS[path]
        _require(descriptor["kind"] == kind and descriptor["required"] is required, "bundle_schema_error", f"descriptor semantics do not match {path}")
        size = _integer(descriptor["size_bytes"], "file.size_bytes", minimum=0)
        _require(size <= _member_limit(path), "unsafe_bundle_archive", f"{path} exceeds its V1 size limit")
        digest = _sha256(descriptor["sha256"], "file.sha256")
        descriptors[path] = (kind, required, size, digest)

    _require("paper.pdf" in descriptors and "metadata.json" in descriptors, "bundle_schema_error", "manifest must declare the required core files")
    _require(tuple(descriptors) == tuple(path for path in _FILE_ORDER if path in descriptors), "bundle_schema_error", "manifest files are not in V1 order")
    return {**manifest, "paper": paper_payload}, paper_payload, descriptors


def _quad(value: object, field: str) -> dict[str, Any]:
    quad = _keys(value, {"x", "y", "width", "height"}, field)
    return {
        "x": _number(quad["x"], f"{field}.x", minimum=0, maximum=1),
        "y": _number(quad["y"], f"{field}.y", minimum=0, maximum=1),
        "width": _number(quad["width"], f"{field}.width", minimum=0, maximum=1, strict_positive=True),
        "height": _number(quad["height"], f"{field}.height", minimum=0, maximum=1, strict_positive=True),
    }


def _parse_metadata(value: object) -> dict[str, Any]:
    # Import lazily because application.__init__ keeps the historical eager
    # service export, while the validator itself is imported by that service.
    from caeliae_read.application.paper_metadata import PaperMetadataNormalizationError, normalize_paper_metadata

    payload = _keys(value, {"schema", "version", "state", *_METADATA_FIELDS}, "metadata")
    _require(payload["schema"] == METADATA_SCHEMA, "bundle_schema_error", "metadata schema is unsupported")
    _validate_version(payload["version"])
    state = _string(payload["state"], "metadata.state")
    _require(state in {"absent", "present"}, "bundle_schema_error", "metadata.state is invalid")
    raw_fields = {field: payload[field] for field in _METADATA_FIELDS}
    if state == "absent":
        _require(all(value is None for value in raw_fields.values()), "bundle_schema_error", "absent metadata must contain only null fields")
    try:
        normalized = normalize_paper_metadata(raw_fields)
    except PaperMetadataNormalizationError as error:
        raise BundleValidationError("bundle_schema_error", "metadata values violate the current metadata contract") from error
    _require(normalized == raw_fields, "bundle_schema_error", "metadata values must already be canonical")
    return {"state": state, **raw_fields}


def _parse_annotations(value: object, page_count: int) -> tuple[dict[str, Any], ...]:
    payload = _keys(value, {"schema", "version", "annotations"}, "annotations")
    _require(payload["schema"] == ANNOTATIONS_SCHEMA, "bundle_schema_error", "annotation schema is unsupported")
    _validate_version(payload["version"])
    raw_items = payload["annotations"]
    _require(isinstance(raw_items, list), "bundle_schema_error", "annotations must be an array")
    result: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, raw in enumerate(raw_items):
        item = _keys(raw, set(_ANNOTATION_FIELDS), f"annotations[{index}]")
        annotation_id = _uuid(item["annotation_id"], f"annotations[{index}].annotation_id")
        _require(annotation_id not in ids, "bundle_schema_error", "annotations contain duplicate annotation_id values")
        ids.add(annotation_id)
        author = _string(item["author"], f"annotations[{index}].author")
        _require(author in _AUTHORS, "bundle_schema_error", "annotation author is invalid")
        style = item["style_key"]
        if style is not None:
            _require(isinstance(style, str) and style in _STYLE_KEYS, "bundle_schema_error", "annotation style_key is invalid")
        mark_type = _string(item["mark_type"], f"annotations[{index}].mark_type")
        _require(mark_type in _MARK_TYPES, "bundle_schema_error", "annotation mark_type is invalid")
        if author == "assistant":
            _require(style is None and mark_type == "underline", "bundle_schema_error", "assistant annotation provenance is invalid")
        else:
            _require(style in _STYLE_KEYS, "bundle_schema_error", "user annotation style_key is required")
        page = _integer(item["page_number"], f"annotations[{index}].page_number", minimum=1)
        _require(page <= page_count, "bundle_schema_error", "annotation page is outside the paper")
        quads = item["normalized_quads"]
        _require(isinstance(quads, list) and 1 <= len(quads) <= 64, "bundle_schema_error", "annotation normalized_quads is invalid")
        parsed = {
            "annotation_id": annotation_id,
            "thread_id": _uuid(item["thread_id"], f"annotations[{index}].thread_id"),
            "page_number": page,
            "exact_text": _string(item["exact_text"], f"annotations[{index}].exact_text", max_length=2_000),
            "prefix": _string(item["prefix"], f"annotations[{index}].prefix", allow_empty=True, max_length=160),
            "suffix": _string(item["suffix"], f"annotations[{index}].suffix", allow_empty=True, max_length=160),
            "normalized_quads": [_quad(quad, f"annotations[{index}].normalized_quads[{quad_index}]") for quad_index, quad in enumerate(quads)],
            "page_width": _number(item["page_width"], f"annotations[{index}].page_width", minimum=0, maximum=100_000, strict_positive=True),
            "page_height": _number(item["page_height"], f"annotations[{index}].page_height", minimum=0, maximum=100_000, strict_positive=True),
            "rotation": _integer(item["rotation"], f"annotations[{index}].rotation"),
            "author": author,
            "note": _optional_string(item["note"], f"annotations[{index}].note", max_length=4_000),
            "remember": item["remember"],
            "style_key": style,
            "mark_type": mark_type,
            "created_at": _string(item["created_at"], f"annotations[{index}].created_at"),
            "updated_at": _string(item["updated_at"], f"annotations[{index}].updated_at"),
        }
        _require(parsed["rotation"] in {0, 90, 180, 270}, "bundle_schema_error", "annotation rotation is invalid")
        _require(isinstance(parsed["remember"], bool), "bundle_schema_error", "annotation remember must be boolean")
        result.append(parsed)
    return tuple(result)


def _parse_sticky_notes(value: object, page_count: int) -> tuple[dict[str, Any], ...]:
    payload = _keys(value, {"schema", "version", "sticky_notes"}, "sticky-notes")
    _require(payload["schema"] == STICKY_NOTES_SCHEMA, "bundle_schema_error", "sticky note schema is unsupported")
    _validate_version(payload["version"])
    raw_items = payload["sticky_notes"]
    _require(isinstance(raw_items, list), "bundle_schema_error", "sticky_notes must be an array")
    result: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, raw in enumerate(raw_items):
        item = _keys(raw, set(_STICKY_FIELDS), f"sticky_notes[{index}]")
        note_id = _uuid(item["id"], f"sticky_notes[{index}].id")
        _require(note_id not in ids, "bundle_schema_error", "sticky notes contain duplicate id values")
        ids.add(note_id)
        page = _integer(item["page_number"], f"sticky_notes[{index}].page_number", minimum=1)
        _require(page <= page_count, "bundle_schema_error", "sticky note page is outside the paper")
        style = _string(item["style_key"], f"sticky_notes[{index}].style_key")
        _require(style in _STYLE_KEYS, "bundle_schema_error", "sticky note style_key is invalid")
        result.append({
            "id": note_id,
            "page_number": page,
            "normalized_x": _number(item["normalized_x"], f"sticky_notes[{index}].normalized_x", minimum=0, maximum=1),
            "normalized_y": _number(item["normalized_y"], f"sticky_notes[{index}].normalized_y", minimum=0, maximum=1),
            "text": _string(item["text"], f"sticky_notes[{index}].text", max_length=2_000),
            "style_key": style,
            "created_at": _string(item["created_at"], f"sticky_notes[{index}].created_at"),
            "updated_at": _string(item["updated_at"], f"sticky_notes[{index}].updated_at"),
        })
    return tuple(result)


def _parse_summary_notes(value: object, page_count: int) -> tuple[dict[str, Any], ...]:
    payload = _keys(value, {"schema", "version", "summary_notes"}, "summary-notes")
    _require(payload["schema"] == SUMMARY_NOTES_SCHEMA, "bundle_schema_error", "summary note schema is unsupported")
    _validate_version(payload["version"])
    raw_items = payload["summary_notes"]
    _require(isinstance(raw_items, list), "bundle_schema_error", "summary_notes must be an array")
    result: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, raw in enumerate(raw_items):
        item = _keys(raw, set(_SUMMARY_FIELDS), f"summary_notes[{index}]")
        note_id = _uuid(item["summary_note_id"], f"summary_notes[{index}].summary_note_id")
        _require(note_id not in ids, "bundle_schema_error", "summary notes contain duplicate summary_note_id values")
        ids.add(note_id)
        page = _integer(item["page_number"], f"summary_notes[{index}].page_number", minimum=1)
        _require(page <= page_count, "bundle_schema_error", "summary note page is outside the paper")
        x = item["normalized_x"]
        result.append({
            "summary_note_id": note_id,
            "page_number": page,
            "normalized_y": _number(item["normalized_y"], f"summary_notes[{index}].normalized_y", minimum=0, maximum=1),
            "normalized_x": None if x is None else _number(x, f"summary_notes[{index}].normalized_x", minimum=0, maximum=1),
            "text": _string(item["text"], f"summary_notes[{index}].text", max_length=2_000),
            "created_at": _string(item["created_at"], f"summary_notes[{index}].created_at"),
            "updated_at": _string(item["updated_at"], f"summary_notes[{index}].updated_at"),
        })
    return tuple(result)


def _validate_citation(data: bytes, metadata: dict[str, Any], paper: dict[str, Any]) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BundleValidationError("bundle_schema_error", "citation.bib must be valid UTF-8") from error
    if not metadata["title"]:
        _fail("bundle_schema_error", "citation.bib requires a metadata title")
    domain_metadata = PaperMetadata(
        paper_id="bundle-preview",
        title=metadata["title"],
        authors=metadata["authors"],
        publication_year=metadata["publication_year"],
        venue=metadata["venue"],
        doi=metadata["doi"],
        arxiv_id=metadata["arxiv_id"],
        citation_key=metadata["citation_key"],
        updated_at="bundle-preview",
    )
    domain_paper = Paper(
        paper_id="bundle-preview",
        original_filename=paper["original_filename"],
        size_bytes=paper["size_bytes"],
        page_count=paper["page_count"],
        created_at="bundle-preview",
    )
    try:
        expected = serialize_bibtex_result(
            domain_paper,
            domain_metadata,
            fallback_key=metadata["citation_key"] or portable_fallback_citation_key(paper["content_sha256"]),
        ).text
    except (BibtexSerializationError, ValueError) as error:
        raise BundleValidationError("bundle_schema_error", "citation.bib metadata is invalid") from error
    _require(text == expected, "bundle_integrity_error", "citation.bib does not match canonical metadata")
    return "valid"


def parse_paper_bundle(archive_bytes: bytes) -> ParsedPortableBundle:
    """Validate and parse a complete V1 ZIP without touching the database."""

    _require(isinstance(archive_bytes, bytes), "invalid_bundle", "bundle upload must be bytes")
    _require(len(archive_bytes) <= MAX_ARCHIVE_BYTES, "unsafe_bundle_archive", "bundle archive exceeds the V1 upload limit")
    try:
        archive = zipfile.ZipFile(BytesIO(archive_bytes), mode="r")
    except (zipfile.BadZipFile, OSError, ValueError) as error:
        raise BundleValidationError("invalid_bundle", "bundle is not a valid ZIP archive") from error

    try:
        infos = archive.infolist()
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError) as error:
        archive.close()
        raise BundleValidationError("invalid_bundle", "bundle central directory is invalid") from error

    with archive:
        _require(0 < len(infos) <= MAX_ARCHIVE_ENTRIES, "unsafe_bundle_archive", "bundle entry count exceeds the V1 limit")
        names: list[str] = []
        casefold_names: set[str] = set()
        total_size = 0
        info_by_name: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            _validate_zip_info(info)
            _require(info.filename not in names, "unsafe_bundle_archive", "bundle contains duplicate member names")
            folded = info.filename.casefold()
            _require(folded not in casefold_names, "unsafe_bundle_archive", "bundle contains case-colliding member names")
            names.append(info.filename)
            casefold_names.add(folded)
            info_by_name[info.filename] = info
            total_size += info.file_size
            _require(total_size <= MAX_TOTAL_UNCOMPRESSED_BYTES, "unsafe_bundle_archive", "bundle uncompressed size exceeds the V1 limit")
            _require(info.file_size <= _member_limit(info.filename), "unsafe_bundle_archive", f"{info.filename} exceeds its V1 size limit")

        allowed_names = {"manifest.json", *_FILE_SPECS}
        _require(set(names) <= allowed_names, "unsafe_bundle_archive", "bundle contains an unexpected member")
        _require("manifest.json" in info_by_name, "bundle_schema_error", "bundle is missing manifest.json")
        try:
            manifest_bytes = archive.read(info_by_name["manifest.json"])
        except (zipfile.BadZipFile, RuntimeError, OSError) as error:
            raise BundleValidationError("bundle_integrity_error", "manifest.json could not be read") from error
        manifest, paper, descriptors = _validate_manifest(_parse_json(manifest_bytes, "manifest.json"))
        business_names = set(names) - {"manifest.json"}
        _require(business_names == set(descriptors), "bundle_schema_error", "archive members and manifest declarations differ")

        payloads: dict[str, bytes] = {}
        for path, (_, _, expected_size, expected_sha256) in descriptors.items():
            try:
                data = archive.read(info_by_name[path])
            except (zipfile.BadZipFile, RuntimeError, OSError) as error:
                raise BundleValidationError("bundle_integrity_error", f"{path} could not be read") from error
            _require(len(data) == expected_size, "bundle_integrity_error", f"{path} size does not match its manifest")
            _require(sha256_bytes(data) == expected_sha256, "bundle_integrity_error", f"{path} checksum does not match its manifest")
            payloads[path] = data

        pdf = payloads["paper.pdf"]
        _require(pdf.startswith(b"%PDF-"), "bundle_integrity_error", "paper.pdf is not a PDF")
        _require(len(pdf) == paper["size_bytes"] and sha256_bytes(pdf) == paper["content_sha256"], "bundle_integrity_error", "paper.pdf does not match manifest paper identity")
        try:
            page_count = len(PdfReader(BytesIO(pdf), strict=False).pages)
        except Exception as error:
            raise BundleValidationError("bundle_integrity_error", "paper.pdf could not be parsed") from error
        _require(page_count == paper["page_count"] and page_count >= 1, "bundle_integrity_error", "paper.pdf page count does not match manifest paper identity")

        metadata = _parse_metadata(_parse_json(payloads["metadata.json"], "metadata.json"))
        annotations = _parse_annotations(_parse_json(payloads["annotations.json"], "annotations.json"), page_count) if "annotations.json" in payloads else ()
        sticky_notes = _parse_sticky_notes(_parse_json(payloads["sticky-notes.json"], "sticky-notes.json"), page_count) if "sticky-notes.json" in payloads else ()
        summary_notes = _parse_summary_notes(_parse_json(payloads["summary-notes.json"], "summary-notes.json"), page_count) if "summary-notes.json" in payloads else ()
        citation_status = _validate_citation(payloads["citation.bib"], metadata, paper) if "citation.bib" in payloads else "absent"

        return ParsedPortableBundle(
            manifest=manifest,
            paper=paper,
            paper_pdf=pdf,
            metadata=metadata,
            annotations=annotations,
            sticky_notes=sticky_notes,
            summary_notes=summary_notes,
            citation_bibtex=citation_status,
        )


__all__ = [
    "MAX_ARCHIVE_BYTES",
    "MAX_ARCHIVE_ENTRIES",
    "MAX_CITATION_BYTES",
    "MAX_COMPRESSION_RATIO",
    "MAX_JSON_BYTES",
    "MAX_PDF_BYTES",
    "MAX_TOTAL_UNCOMPRESSED_BYTES",
    "BundleValidationError",
    "ParsedPortableBundle",
    "parse_paper_bundle",
]
