"""Pure Portable Paper Bundle V1 domain serialization.

This module deliberately has no database, filesystem, network, FastAPI, or
runtime-environment dependency.  It defines the canonical JSON payloads for
paper metadata, annotations, sticky notes, Summary Notes, and the bundle
manifest.  The PDF bytes and these records are canonical; ``citation.bib`` is
an optional derived interoperability artifact.  Vocabulary, Reading, and
workspace state are intentionally deferred.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import math
import re
from collections.abc import Iterable, Mapping
from typing import Any
import unicodedata
import zipfile

from caeliae_read.domain.models import Annotation, PaperMetadata, StickyNote, SummaryNote


BUNDLE_FORMAT = "caeliae-paper-bundle"
BUNDLE_VERSION = "1.0"
METADATA_SCHEMA = "caeliae-paper-metadata"
ANNOTATIONS_SCHEMA = "caeliae-paper-annotations"
STICKY_NOTES_SCHEMA = "caeliae-paper-sticky-notes"
SUMMARY_NOTES_SCHEMA = "caeliae-paper-summary-notes"

PAPER_FILE_KIND = "paper"
METADATA_FILE_KIND = "metadata"
ANNOTATIONS_FILE_KIND = "annotations"
STICKY_NOTES_FILE_KIND = "sticky-notes"
SUMMARY_NOTES_FILE_KIND = "summary-notes"
CITATION_FILE_KIND = "derived-bibtex"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FILE_SPECS: dict[str, tuple[str, bool]] = {
    "paper.pdf": (PAPER_FILE_KIND, True),
    "metadata.json": (METADATA_FILE_KIND, True),
    "annotations.json": (ANNOTATIONS_FILE_KIND, False),
    "sticky-notes.json": (STICKY_NOTES_FILE_KIND, False),
    "summary-notes.json": (SUMMARY_NOTES_FILE_KIND, False),
    "citation.bib": (CITATION_FILE_KIND, False),
}
_FILE_ORDER = tuple(_FILE_SPECS)
_AUTHORS = {"user", "assistant"}
_MARK_TYPES = {"underline", "highlight"}
_STYLE_KEYS = {"primary", "secondary", "tertiary"}


class BundleSerializationError(ValueError):
    """Raised when a value cannot satisfy the Portable Bundle V1 contract."""


def canonical_json_bytes(value: object) -> bytes:
    """Return deterministic UTF-8 JSON with exactly one final newline."""

    import json

    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError) as error:
        raise BundleSerializationError("value is not valid canonical JSON") from error
    return (rendered + "\n").encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase SHA-256 digest of exact raw bytes."""

    if not isinstance(data, bytes):
        raise BundleSerializationError("checksum input must be bytes")
    return hashlib.sha256(data).hexdigest()


def _require_string(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise BundleSerializationError(f"{field} must be a string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise BundleSerializationError(f"{field} must be valid UTF-8") from error
    if not allow_empty and not value:
        raise BundleSerializationError(f"{field} must not be empty")
    return value


def _require_int(value: object, field: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BundleSerializationError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise BundleSerializationError(f"{field} must be at least {minimum}")
    return value


def _require_number(value: object, field: str, *, minimum: float | None = None, maximum: float | None = None, strict_positive: bool = False) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise BundleSerializationError(f"{field} must be a finite number")
    numeric = float(value)
    if strict_positive and numeric <= 0:
        raise BundleSerializationError(f"{field} must be greater than zero")
    if minimum is not None and numeric < minimum:
        raise BundleSerializationError(f"{field} must be at least {minimum}")
    if maximum is not None and numeric > maximum:
        raise BundleSerializationError(f"{field} must be at most {maximum}")
    return value


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, field, allow_empty=True)


def _validate_basename(value: object, field: str) -> str:
    name = _require_string(value, field)
    if len(name) > 255 or name in {".", ".."} or "\x00" in name:
        raise BundleSerializationError(f"{field} must be a basename-level filename")
    if "/" in name or "\\" in name or re.match(r"^[A-Za-z]:", name):
        raise BundleSerializationError(f"{field} must not contain a local path")
    return name


def _validate_sha256(value: object, field: str = "sha256") -> str:
    digest = _require_string(value, field)
    if _SHA256_RE.fullmatch(digest) is None:
        raise BundleSerializationError(f"{field} must be 64 lowercase hexadecimal characters")
    return digest


@dataclass(frozen=True)
class PaperIdentity:
    """Portable paper identity; local paper_id/storage_key are excluded."""

    content_sha256: str
    mime_type: str
    size_bytes: int
    page_count: int
    original_filename: str

    def __post_init__(self) -> None:
        _validate_sha256(self.content_sha256, "content_sha256")
        if self.mime_type != "application/pdf":
            raise BundleSerializationError("V1 paper mime_type must be application/pdf")
        _require_int(self.size_bytes, "size_bytes", minimum=0)
        _require_int(self.page_count, "page_count", minimum=1)
        _validate_basename(self.original_filename, "original_filename")

    def to_dict(self) -> dict[str, object]:
        return {
            "content_sha256": self.content_sha256,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "page_count": self.page_count,
            "original_filename": self.original_filename,
        }


@dataclass(frozen=True)
class BundleFileDescriptor:
    """Description of one final bundle member, excluding manifest self-hash."""

    path: str
    kind: str
    required: bool
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        spec = _FILE_SPECS.get(self.path)
        if spec is None:
            raise BundleSerializationError("bundle file path is not supported by V1")
        expected_kind, expected_required = spec
        if self.kind != expected_kind or self.required != expected_required:
            raise BundleSerializationError(f"descriptor semantics do not match {self.path}")
        _require_int(self.size_bytes, "file.size_bytes", minimum=0)
        _validate_sha256(self.sha256, "file.sha256")

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "kind": self.kind,
            "required": self.required,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class BundleManifest:
    paper: PaperIdentity
    files: tuple[BundleFileDescriptor, ...]
    exported_at: str

    def __post_init__(self) -> None:
        _require_string(self.exported_at, "exported_at")
        descriptors = tuple(self.files)
        if len({item.path for item in descriptors}) != len(descriptors):
            raise BundleSerializationError("manifest files must not contain duplicate paths")
        paths = {item.path for item in descriptors}
        if {"paper.pdf", "metadata.json"} - paths:
            raise BundleSerializationError("manifest must describe paper.pdf and metadata.json")
        if tuple(item.path for item in descriptors) != tuple(sorted(paths, key=_FILE_ORDER.index)):
            raise BundleSerializationError("manifest file descriptors must use V1 ordering")

    def to_dict(self) -> dict[str, object]:
        return {
            "format": BUNDLE_FORMAT,
            "version": BUNDLE_VERSION,
            "exported_at": self.exported_at,
            "paper": self.paper.to_dict(),
            "files": [item.to_dict() for item in self.files],
        }


def build_file_descriptor(*, path: str, kind: str, required: bool, data: bytes) -> BundleFileDescriptor:
    """Build a descriptor from the exact final serialized member bytes."""

    if not isinstance(data, bytes):
        raise BundleSerializationError("file data must be bytes")
    return BundleFileDescriptor(
        path=path,
        kind=kind,
        required=required,
        size_bytes=len(data),
        sha256=sha256_bytes(data),
    )


def build_manifest(*, paper: PaperIdentity, files: Iterable[BundleFileDescriptor], exported_at: str) -> BundleManifest:
    """Build a manifest with the fixed V1 file order."""

    descriptors = tuple(files)
    known = {item.path: item for item in descriptors}
    if len(known) != len(descriptors):
        raise BundleSerializationError("manifest files must not contain duplicate paths")
    if set(known) - set(_FILE_SPECS):
        raise BundleSerializationError("manifest contains an unsupported file path")
    ordered = tuple(known[path] for path in _FILE_ORDER if path in known)
    return BundleManifest(paper=paper, files=ordered, exported_at=exported_at)


def serialize_manifest(manifest: BundleManifest) -> bytes:
    """Serialize a manifest; its own checksum is intentionally absent."""

    if not isinstance(manifest, BundleManifest):
        raise BundleSerializationError("manifest must be a BundleManifest")
    return canonical_json_bytes(manifest.to_dict())


def build_bundle_zip(*, manifest: BundleManifest, payloads: Mapping[str, bytes]) -> bytes:
    """Build a deterministic ZIP from the final payload bytes and manifest."""

    manifest_bytes = serialize_manifest(manifest)
    described_paths = {descriptor.path for descriptor in manifest.files}
    if set(payloads) != described_paths:
        raise BundleSerializationError("manifest files must exactly match bundle payloads")
    for path, data in payloads.items():
        if path not in _FILE_SPECS or not isinstance(data, bytes):
            raise BundleSerializationError("bundle payload is invalid")
        descriptor = next(item for item in manifest.files if item.path == path)
        if descriptor.size_bytes != len(data) or descriptor.sha256 != sha256_bytes(data):
            raise BundleSerializationError(f"manifest checksum does not match {path}")

    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in ("manifest.json", *_FILE_ORDER):
            if path == "manifest.json":
                data = manifest_bytes
            elif path in payloads:
                data = payloads[path]
            else:
                continue
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0
            info.flag_bits = 0x800
            archive.writestr(info, data)
    return output.getvalue()


def bundle_attachment_filename(original_filename: str, content_sha256: str) -> str:
    """Return a bounded ASCII attachment name without local path information."""

    _validate_sha256(content_sha256, "content_sha256")
    if not isinstance(original_filename, str):
        original_filename = ""
    stem = original_filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem)
    stem = re.sub(r"-+", "-", stem).strip("._-")[:80]
    if not stem:
        stem = f"caeliae-paper-{content_sha256[:12]}"
    return f"{stem}.caeliae-paper.zip"


def _metadata_dict(metadata: PaperMetadata | None) -> dict[str, object]:
    if metadata is None:
        state = "absent"
        values: dict[str, object] = {}
    elif isinstance(metadata, PaperMetadata):
        state = "present"
        values = {
            "title": metadata.title,
            "authors": metadata.authors,
            "publication_year": metadata.publication_year,
            "venue": metadata.venue,
            "doi": metadata.doi,
            "arxiv_id": metadata.arxiv_id,
            "citation_key": metadata.citation_key,
        }
    else:
        raise BundleSerializationError("metadata must be PaperMetadata or None")

    if values.get("authors") is not None:
        authors = values["authors"]
        if not isinstance(authors, list) or any(not isinstance(item, str) for item in authors):
            raise BundleSerializationError("metadata.authors must be an array of strings or null")
    if values.get("publication_year") is not None:
        _require_int(values["publication_year"], "metadata.publication_year")
    for field in ("title", "venue", "doi", "arxiv_id", "citation_key"):
        _optional_string(values.get(field), f"metadata.{field}")
    return {
        "schema": METADATA_SCHEMA,
        "version": BUNDLE_VERSION,
        "state": state,
        "title": values.get("title"),
        "authors": values.get("authors"),
        "publication_year": values.get("publication_year"),
        "venue": values.get("venue"),
        "doi": values.get("doi"),
        "arxiv_id": values.get("arxiv_id"),
        "citation_key": values.get("citation_key"),
    }


def serialize_metadata(metadata: PaperMetadata | None) -> bytes:
    """Serialize absent, present-empty, or populated metadata without fallback."""

    return canonical_json_bytes(_metadata_dict(metadata))


def _quad_dict(quad: Mapping[str, object], index: int) -> dict[str, object]:
    if not isinstance(quad, Mapping):
        raise BundleSerializationError(f"annotation.normalized_quads[{index}] must be an object")
    if set(quad) != {"x", "y", "width", "height"}:
        raise BundleSerializationError(f"annotation.normalized_quads[{index}] has invalid fields")
    result: dict[str, object] = {}
    for field in ("x", "y"):
        result[field] = _require_number(quad[field], f"annotation.normalized_quads[{index}].{field}", minimum=0, maximum=1)
    for field in ("width", "height"):
        result[field] = _require_number(quad[field], f"annotation.normalized_quads[{index}].{field}", minimum=0, maximum=1, strict_positive=True)
    return result


def _annotation_dict(annotation: Annotation) -> dict[str, object]:
    if not isinstance(annotation, Annotation):
        raise BundleSerializationError("annotations must contain Annotation values")
    _require_string(annotation.annotation_id, "annotation.annotation_id")
    _require_string(annotation.thread_id, "annotation.thread_id")
    _require_int(annotation.page_number, "annotation.page_number", minimum=1)
    _require_string(annotation.exact_text, "annotation.exact_text")
    _require_string(annotation.prefix, "annotation.prefix", allow_empty=True)
    _require_string(annotation.suffix, "annotation.suffix", allow_empty=True)
    if not isinstance(annotation.normalized_quads, list) or not annotation.normalized_quads:
        raise BundleSerializationError("annotation.normalized_quads must be a non-empty array")
    quads = [_quad_dict(quad, index) for index, quad in enumerate(annotation.normalized_quads)]
    _require_number(annotation.page_width, "annotation.page_width", strict_positive=True)
    _require_number(annotation.page_height, "annotation.page_height", strict_positive=True)
    if annotation.rotation not in {0, 90, 180, 270}:
        raise BundleSerializationError("annotation.rotation is invalid")
    if annotation.author not in _AUTHORS:
        raise BundleSerializationError("annotation.author is invalid")
    _optional_string(annotation.note, "annotation.note")
    if not isinstance(annotation.remember, bool):
        raise BundleSerializationError("annotation.remember must be boolean")
    if annotation.style_key is not None and annotation.style_key not in _STYLE_KEYS:
        raise BundleSerializationError("annotation.style_key is invalid")
    if annotation.mark_type not in _MARK_TYPES:
        raise BundleSerializationError("annotation.mark_type is invalid")
    _require_string(annotation.created_at, "annotation.created_at")
    _require_string(annotation.updated_at, "annotation.updated_at")
    return {
        "annotation_id": annotation.annotation_id,
        "thread_id": annotation.thread_id,
        "page_number": annotation.page_number,
        "exact_text": annotation.exact_text,
        "prefix": annotation.prefix,
        "suffix": annotation.suffix,
        "normalized_quads": quads,
        "page_width": annotation.page_width,
        "page_height": annotation.page_height,
        "rotation": annotation.rotation,
        "author": annotation.author,
        "note": annotation.note,
        "remember": annotation.remember,
        "style_key": annotation.style_key,
        "mark_type": annotation.mark_type,
        "created_at": annotation.created_at,
        "updated_at": annotation.updated_at,
    }


def _annotation_sort_key(item: dict[str, object]) -> tuple[object, ...]:
    first = item["normalized_quads"][0]  # type: ignore[index]
    geometry = canonical_json_bytes(item["normalized_quads"])
    return (item["page_number"], first["x"], first["y"], item["created_at"], item["annotation_id"], geometry)


def serialize_annotations(annotations: Iterable[Annotation]) -> bytes:
    """Serialize all annotations, including assistant provenance, deterministically."""

    items = [_annotation_dict(item) for item in annotations]
    ids = [str(item["annotation_id"]) for item in items]
    if len(set(ids)) != len(ids):
        raise BundleSerializationError("annotations must not contain duplicate annotation_id values")
    items.sort(key=_annotation_sort_key)
    return canonical_json_bytes({"schema": ANNOTATIONS_SCHEMA, "version": BUNDLE_VERSION, "annotations": items})


def _sticky_dict(note: StickyNote) -> dict[str, object]:
    if not isinstance(note, StickyNote):
        raise BundleSerializationError("sticky notes must contain StickyNote values")
    _require_string(note.id, "sticky.id")
    _require_int(note.page, "sticky.page_number", minimum=1)
    x = _require_number(note.x, "sticky.normalized_x", minimum=0, maximum=1)
    y = _require_number(note.y, "sticky.normalized_y", minimum=0, maximum=1)
    _require_string(note.text, "sticky.text")
    if note.style_key not in _STYLE_KEYS:
        raise BundleSerializationError("sticky.style_key is invalid")
    _require_string(note.created_at, "sticky.created_at")
    _require_string(note.updated_at, "sticky.updated_at")
    return {
        "id": note.id,
        "page_number": note.page,
        "normalized_x": x,
        "normalized_y": y,
        "text": note.text,
        "style_key": note.style_key,
        "created_at": note.created_at,
        "updated_at": note.updated_at,
    }


def serialize_sticky_notes(notes: Iterable[StickyNote]) -> bytes:
    items = [_sticky_dict(note) for note in notes]
    ids = [str(item["id"]) for item in items]
    if len(set(ids)) != len(ids):
        raise BundleSerializationError("sticky notes must not contain duplicate id values")
    items.sort(key=lambda item: (item["page_number"], item["normalized_y"], item["normalized_x"], item["created_at"], item["id"]))
    return canonical_json_bytes({"schema": STICKY_NOTES_SCHEMA, "version": BUNDLE_VERSION, "sticky_notes": items})


def _summary_dict(note: SummaryNote) -> dict[str, object]:
    if not isinstance(note, SummaryNote):
        raise BundleSerializationError("summary notes must contain SummaryNote values")
    _require_string(note.summary_note_id, "summary.summary_note_id")
    _require_int(note.page_number, "summary.page_number", minimum=1)
    y = _require_number(note.normalized_y, "summary.normalized_y", minimum=0, maximum=1)
    x = None if note.normalized_x is None else _require_number(note.normalized_x, "summary.normalized_x", minimum=0, maximum=1)
    _require_string(note.text, "summary.text")
    _require_string(note.created_at, "summary.created_at")
    _require_string(note.updated_at, "summary.updated_at")
    return {
        "summary_note_id": note.summary_note_id,
        "page_number": note.page_number,
        "normalized_y": y,
        "normalized_x": x,
        "text": note.text,
        "created_at": note.created_at,
        "updated_at": note.updated_at,
    }


def serialize_summary_notes(notes: Iterable[SummaryNote]) -> bytes:
    items = [_summary_dict(note) for note in notes]
    ids = [str(item["summary_note_id"]) for item in items]
    if len(set(ids)) != len(ids):
        raise BundleSerializationError("summary notes must not contain duplicate summary_note_id values")
    items.sort(key=lambda item: (item["page_number"], item["normalized_y"], item["created_at"], item["summary_note_id"]))
    return canonical_json_bytes({"schema": SUMMARY_NOTES_SCHEMA, "version": BUNDLE_VERSION, "summary_notes": items})


def citation_bib_is_available(metadata: PaperMetadata | None) -> bool:
    """Return whether the existing BibTeX serializer can produce citation.bib."""

    return isinstance(metadata, PaperMetadata) and isinstance(metadata.title, str) and bool(metadata.title.strip())


__all__ = [
    "ANNOTATIONS_SCHEMA",
    "ANNOTATIONS_FILE_KIND",
    "BUNDLE_FORMAT",
    "BUNDLE_VERSION",
    "BundleFileDescriptor",
    "BundleManifest",
    "BundleSerializationError",
    "build_bundle_zip",
    "bundle_attachment_filename",
    "CITATION_FILE_KIND",
    "METADATA_FILE_KIND",
    "METADATA_SCHEMA",
    "PaperIdentity",
    "PAPER_FILE_KIND",
    "STICKY_NOTES_FILE_KIND",
    "STICKY_NOTES_SCHEMA",
    "SUMMARY_NOTES_FILE_KIND",
    "SUMMARY_NOTES_SCHEMA",
    "build_file_descriptor",
    "build_manifest",
    "canonical_json_bytes",
    "citation_bib_is_available",
    "serialize_annotations",
    "serialize_manifest",
    "serialize_metadata",
    "serialize_sticky_notes",
    "serialize_summary_notes",
    "sha256_bytes",
]
