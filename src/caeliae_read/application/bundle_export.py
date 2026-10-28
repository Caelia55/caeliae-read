"""Pure assembly of Portable Paper Bundle V1 bytes."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable

from caeliae_read.bibtex import (
    BibtexSerializationError,
    portable_fallback_citation_key,
    serialize_bibtex_result,
)
from caeliae_read.bundle import (
    ANNOTATIONS_FILE_KIND,
    CITATION_FILE_KIND,
    METADATA_FILE_KIND,
    PAPER_FILE_KIND,
    STICKY_NOTES_FILE_KIND,
    SUMMARY_NOTES_FILE_KIND,
    BundleManifest,
    BundleSerializationError,
    PaperIdentity,
    build_bundle_zip,
    build_file_descriptor,
    build_manifest,
    bundle_attachment_filename,
    citation_bib_is_available,
    serialize_annotations,
    serialize_metadata,
    serialize_sticky_notes,
    serialize_summary_notes,
)
from caeliae_read.domain.models import Annotation, Paper, PaperMetadata, StickyNote, SummaryNote

__all__ = ["PaperBundleExport", "assemble_paper_bundle"]


@dataclass(frozen=True)
class PaperBundleExport:
    """The response bytes and safe attachment name for one assembled bundle."""

    content: bytes
    filename: str
    manifest: BundleManifest


def assemble_paper_bundle(
    *,
    paper: Paper,
    content_sha256: str,
    mime_type: str,
    pdf_bytes: bytes,
    metadata: PaperMetadata | None,
    annotations: Iterable[Annotation],
    sticky_notes: Iterable[StickyNote],
    summary_notes: Iterable[SummaryNote],
    exported_at: str,
) -> PaperBundleExport:
    """Assemble a V1 bundle from already-loaded, validated records and bytes."""

    try:
        identity = PaperIdentity(
            content_sha256=content_sha256,
            mime_type=mime_type,
            size_bytes=len(pdf_bytes),
            page_count=paper.page_count,
            original_filename=paper.original_filename,
        )
        annotation_items = tuple(annotations)
        sticky_items = tuple(sticky_notes)
        summary_items = tuple(summary_notes)
        payloads: dict[str, bytes] = {
            "paper.pdf": pdf_bytes,
            "metadata.json": serialize_metadata(metadata),
        }
        if annotation_items:
            payloads["annotations.json"] = serialize_annotations(annotation_items)
        if sticky_items:
            payloads["sticky-notes.json"] = serialize_sticky_notes(sticky_items)
        if summary_items:
            payloads["summary-notes.json"] = serialize_summary_notes(summary_items)
        if citation_bib_is_available(metadata):
            try:
                assert metadata is not None
                result = serialize_bibtex_result(
                    paper,
                    metadata,
                    fallback_key=(
                        metadata.citation_key
                        or portable_fallback_citation_key(content_sha256)
                    ),
                )
            except BibtexSerializationError:
                result = None
            if result is not None:
                payloads["citation.bib"] = result.text.encode("utf-8")

        descriptors = []
        for path, kind, required in (
            ("paper.pdf", PAPER_FILE_KIND, True),
            ("metadata.json", METADATA_FILE_KIND, True),
            ("annotations.json", ANNOTATIONS_FILE_KIND, False),
            ("sticky-notes.json", STICKY_NOTES_FILE_KIND, False),
            ("summary-notes.json", SUMMARY_NOTES_FILE_KIND, False),
            ("citation.bib", CITATION_FILE_KIND, False),
        ):
            if path in payloads:
                descriptors.append(
                    build_file_descriptor(
                        path=path,
                        kind=kind,
                        required=required,
                        data=payloads[path],
                    )
                )
        manifest = build_manifest(
            paper=identity,
            files=descriptors,
            exported_at=exported_at,
        )
        archive = build_bundle_zip(manifest=manifest, payloads=payloads)
        return PaperBundleExport(
            content=archive,
            filename=bundle_attachment_filename(paper.original_filename, content_sha256),
            manifest=manifest,
        )
    except BundleSerializationError:
        raise
