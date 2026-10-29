"""Read-only target-library comparison for Portable Paper Bundle previews."""

from __future__ import annotations

from typing import Any

from caeliae_read.bundle_import import ParsedPortableBundle
from caeliae_read.domain.models import Annotation, PaperMetadata, StickyNote, SummaryNote
from caeliae_read.storage.repository import CaeliaeReadRepository


def _metadata_fields(metadata: PaperMetadata | None) -> dict[str, Any] | None:
    if metadata is None:
        return None
    return {
        "title": metadata.title,
        "authors": metadata.authors,
        "publication_year": metadata.publication_year,
        "venue": metadata.venue,
        "doi": metadata.doi,
        "arxiv_id": metadata.arxiv_id,
        "citation_key": metadata.citation_key,
    }


def _annotation_fields(annotation: Annotation) -> dict[str, Any]:
    return {
        "annotation_id": annotation.annotation_id,
        "thread_id": annotation.thread_id,
        "page_number": annotation.page_number,
        "exact_text": annotation.exact_text,
        "prefix": annotation.prefix,
        "suffix": annotation.suffix,
        "normalized_quads": annotation.normalized_quads,
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


def _sticky_fields(note: StickyNote) -> dict[str, Any]:
    return {
        "id": note.id,
        "page_number": note.page,
        "normalized_x": note.x,
        "normalized_y": note.y,
        "text": note.text,
        "style_key": note.style_key,
        "created_at": note.created_at,
        "updated_at": note.updated_at,
    }


def _summary_fields(note: SummaryNote) -> dict[str, Any]:
    return {
        "summary_note_id": note.summary_note_id,
        "page_number": note.page_number,
        "normalized_y": note.normalized_y,
        "normalized_x": note.normalized_x,
        "text": note.text,
        "created_at": note.created_at,
        "updated_at": note.updated_at,
    }


def _collection_preview(
    incoming: tuple[dict[str, Any], ...],
    existing: list[Any],
    *,
    key: str,
    existing_fields,
) -> dict[str, Any]:
    if not existing:
        return {
            "incoming": len(incoming),
            "new": len(incoming),
            "identical": 0,
            "conflict": 0,
            "conflicts": [],
        }
    existing_by_id = {str(getattr(item, key)): item for item in existing}
    new_count = 0
    identical_count = 0
    conflicts: list[str] = []
    for item in incoming:
        identifier = str(item[key])
        current = existing_by_id.get(identifier)
        if current is None:
            new_count += 1
        elif item == existing_fields(current):
            identical_count += 1
        else:
            conflicts.append(identifier)
    return {
        "incoming": len(incoming),
        "new": new_count,
        "identical": identical_count,
        "conflict": len(conflicts),
        "conflicts": conflicts,
    }


def _metadata_preview(incoming: dict[str, Any], current: PaperMetadata | None, *, target_exists: bool) -> tuple[dict[str, Any], bool]:
    if incoming["state"] == "absent":
        return {"status": "incoming_absent", "differences": []}, False
    incoming_fields = {field: incoming[field] for field in ("title", "authors", "publication_year", "venue", "doi", "arxiv_id", "citation_key")}
    if not target_exists or current is None:
        return {"status": "would_create", "differences": []}, False
    current_fields = _metadata_fields(current)
    assert current_fields is not None
    differences = [
        {"field": field, "current": current_fields[field], "incoming": incoming_fields[field]}
        for field in incoming_fields
        if current_fields[field] != incoming_fields[field]
    ]
    return {"status": "identical" if not differences else "conflict", "differences": differences}, bool(differences)


def build_bundle_preview(repository: CaeliaeReadRepository, bundle: ParsedPortableBundle) -> dict[str, Any]:
    """Compare a validated bundle to the target repository without mutation."""

    existing = repository.find_paper_by_hash(bundle.paper["content_sha256"])
    target_exists = existing is not None
    target_paper_id: str | None = None
    if target_exists:
        assert existing is not None
        target_paper_id = existing[0].paper_id

    metadata = repository.get_paper_metadata(target_paper_id) if target_paper_id else None
    metadata_preview, metadata_conflict = _metadata_preview(bundle.metadata, metadata, target_exists=target_exists)

    annotations = repository.list_annotations(target_paper_id) if target_paper_id else []
    sticky_notes = repository.list_sticky_notes(target_paper_id) if target_paper_id else []
    summary_notes = repository.list_summary_notes(target_paper_id) if target_paper_id else []
    annotation_preview = _collection_preview(bundle.annotations, annotations, key="annotation_id", existing_fields=_annotation_fields)
    sticky_preview = _collection_preview(bundle.sticky_notes, sticky_notes, key="id", existing_fields=_sticky_fields)
    summary_preview = _collection_preview(bundle.summary_notes, summary_notes, key="summary_note_id", existing_fields=_summary_fields)

    blocking: list[str] = []
    if metadata_conflict:
        blocking.append("metadata")
    if annotation_preview["conflict"]:
        blocking.append("annotations")
    if sticky_preview["conflict"]:
        blocking.append("sticky_notes")
    if summary_preview["conflict"]:
        blocking.append("summary_notes")

    return {
        "valid": True,
        "bundle": {
            "format": bundle.manifest["format"],
            "version": bundle.manifest["version"],
            "exported_at": bundle.manifest["exported_at"],
            "paper": bundle.paper,
        },
        "target": {
            "paper_status": "existing" if target_exists else "new",
            "paper_id": target_paper_id,
            "metadata": metadata_preview,
            "annotations": annotation_preview,
            "sticky_notes": sticky_preview,
            "summary_notes": summary_preview,
        },
        "derived": {"citation_bibtex": bundle.citation_bibtex},
        "can_import": not blocking,
        "blocking_conflicts": blocking,
    }


__all__ = ["build_bundle_preview"]
