from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest

from caeliae_read.bundle import (
    ANNOTATIONS_FILE_KIND,
    BundleFileDescriptor,
    BundleSerializationError,
    CITATION_FILE_KIND,
    METADATA_FILE_KIND,
    PAPER_FILE_KIND,
    PaperIdentity,
    STICKY_NOTES_FILE_KIND,
    SUMMARY_NOTES_FILE_KIND,
    build_bundle_zip,
    build_file_descriptor,
    build_manifest,
    canonical_json_bytes,
    citation_bib_is_available,
    serialize_annotations,
    serialize_manifest,
    serialize_metadata,
    serialize_sticky_notes,
    serialize_summary_notes,
    sha256_bytes,
)
from caeliae_read.domain.models import Annotation, PaperMetadata, StickyNote, SummaryNote


PAPER_HASH = "a" * 64


def identity(**overrides: object) -> PaperIdentity:
    values: dict[str, object] = {
        "content_sha256": PAPER_HASH,
        "mime_type": "application/pdf",
        "size_bytes": 12,
        "page_count": 3,
        "original_filename": "研究论文.pdf",
    }
    values.update(overrides)
    return PaperIdentity(**values)  # type: ignore[arg-type]


def metadata(**overrides: object) -> PaperMetadata:
    values: dict[str, object] = {
        "paper_id": str(uuid4()),
        "title": "研究 {模型}",
        "authors": ["蔡佳颖", "Alice Smith"],
        "publication_year": 2026,
        "venue": "Example Journal",
        "doi": "10.1234/example",
        "arxiv_id": "2601.12345",
        "citation_key": "research2026",
        "updated_at": "2026-10-29T00:00:00Z",
    }
    values.update(overrides)
    return PaperMetadata(**values)  # type: ignore[arg-type]


def annotation(*, annotation_id: str | None = None, page: int = 2, x: float = .2) -> Annotation:
    return Annotation(
        annotation_id=annotation_id or str(uuid4()),
        thread_id=str(uuid4()),
        paper_id=str(uuid4()),
        session_id=str(uuid4()),
        page_number=page,
        exact_text="Selected 文本",
        prefix="before ",
        suffix=" after",
        normalized_quads=[{"x": x, "y": .3, "width": .2, "height": .04}],
        page_width=612,
        page_height=792,
        rotation=0,
        author="assistant",
        note="assistant provenance",
        remember=True,
        style_key=None,
        mark_type="underline",
        created_at="2026-10-29T00:00:01Z",
        updated_at="2026-10-29T00:00:02Z",
    )


def sticky(note_id: str | None = None, *, page: int = 2, x: float = .4) -> StickyNote:
    return StickyNote(
        id=note_id or str(uuid4()),
        paper_id=str(uuid4()),
        page=page,
        x=x,
        y=.6,
        text="便签内容",
        style_key="secondary",
        created_at="2026-10-29T00:00:03Z",
        updated_at="2026-10-29T00:00:04Z",
    )


def summary(note_id: str | None = None, *, x: float | None = None, y: float = .7) -> SummaryNote:
    return SummaryNote(
        summary_note_id=note_id or str(uuid4()),
        paper_id=str(uuid4()),
        page_number=2,
        normalized_y=y,
        normalized_x=x,
        text="页面大意",
        created_at="2026-10-29T00:00:05Z",
        updated_at="2026-10-29T00:00:06Z",
    )


def descriptor(path: str, kind: str, required: bool, data: bytes = b"payload") -> BundleFileDescriptor:
    return build_file_descriptor(path=path, kind=kind, required=required, data=data)


def test_canonical_json_is_utf8_sorted_and_has_one_final_newline() -> None:
    rendered = canonical_json_bytes({"z": "中文", "a": 1})
    assert rendered == '{"a":1,"z":"中文"}\n'.encode("utf-8")
    assert rendered.endswith(b"\n") and not rendered.endswith(b"\n\n")


def test_sha256_and_descriptor_use_exact_bytes() -> None:
    data = "研究".encode("utf-8")
    assert sha256_bytes(data) == hashlib.sha256(data).hexdigest()
    item = descriptor("paper.pdf", PAPER_FILE_KIND, True, data)
    assert item.size_bytes == len(data)
    assert item.sha256 == sha256_bytes(data)


def test_manifest_has_fixed_shape_order_and_no_local_identity() -> None:
    metadata_bytes = serialize_metadata(metadata())
    manifest = build_manifest(
        paper=identity(),
        exported_at="2026-10-29T00:00:00Z",
        files=[
            descriptor("citation.bib", CITATION_FILE_KIND, False),
            descriptor("metadata.json", METADATA_FILE_KIND, True, metadata_bytes),
            descriptor("paper.pdf", PAPER_FILE_KIND, True, b"%PDF-raw"),
            descriptor("summary-notes.json", SUMMARY_NOTES_FILE_KIND, False),
            descriptor("annotations.json", ANNOTATIONS_FILE_KIND, False),
            descriptor("sticky-notes.json", STICKY_NOTES_FILE_KIND, False),
        ],
    )
    output = json.loads(serialize_manifest(manifest))
    assert output["format"] == "caeliae-paper-bundle"
    assert output["version"] == "1.0"
    assert [item["path"] for item in output["files"]] == [
        "paper.pdf", "metadata.json", "annotations.json", "sticky-notes.json", "summary-notes.json", "citation.bib"
    ]
    serialized = serialize_manifest(manifest).decode("utf-8")
    assert "paper_id" not in serialized
    assert "storage_key" not in serialized
    assert "session_id" not in serialized
    assert "manifest.json" not in serialized


def test_manifest_requires_core_files_and_rejects_duplicates() -> None:
    with pytest.raises(BundleSerializationError):
        build_manifest(paper=identity(), files=[descriptor("paper.pdf", PAPER_FILE_KIND, True)], exported_at="now")
    with pytest.raises(BundleSerializationError):
        build_manifest(
            paper=identity(),
            files=[descriptor("paper.pdf", PAPER_FILE_KIND, True), descriptor("metadata.json", METADATA_FILE_KIND, True), descriptor("metadata.json", METADATA_FILE_KIND, True)],
            exported_at="now",
        )


def test_bundle_zip_rejects_manifest_payload_checksum_drift() -> None:
    paper_bytes = b"%PDF-raw"
    metadata_bytes = b"{}\n"
    manifest = build_manifest(
        paper=identity(),
        files=[
            descriptor("paper.pdf", PAPER_FILE_KIND, True, paper_bytes),
            descriptor("metadata.json", METADATA_FILE_KIND, True, metadata_bytes),
        ],
        exported_at="2026-10-29T00:00:00Z",
    )
    with pytest.raises(BundleSerializationError, match="checksum"):
        build_bundle_zip(
            manifest=manifest,
            payloads={"paper.pdf": b"different", "metadata.json": metadata_bytes},
        )


def test_manifest_rejects_unsupported_descriptor_paths_and_bad_descriptor_semantics() -> None:
    with pytest.raises(BundleSerializationError):
        BundleFileDescriptor(path="other.json", kind="metadata", required=False, size_bytes=1, sha256="a" * 64)
    with pytest.raises(BundleSerializationError):
        descriptor("paper.pdf", PAPER_FILE_KIND, False)
    with pytest.raises(BundleSerializationError):
        descriptor("metadata.json", "wrong-kind", True, b"metadata")


def test_manifest_with_only_core_files_omits_optional_descriptors() -> None:
    manifest = build_manifest(
        paper=identity(),
        files=[
            descriptor("metadata.json", METADATA_FILE_KIND, True, serialize_metadata(None)),
            descriptor("paper.pdf", PAPER_FILE_KIND, True, b"%PDF-raw"),
        ],
        exported_at="now",
    )
    assert [item.path for item in manifest.files] == ["paper.pdf", "metadata.json"]


@pytest.mark.parametrize("filename", [r"C:\Users\private\paper.pdf", "/tmp/paper.pdf", "..\\paper.pdf", "../paper.pdf"])
def test_paper_identity_rejects_absolute_or_path_like_filename(filename: str) -> None:
    with pytest.raises(BundleSerializationError):
        identity(original_filename=filename)


def test_paper_identity_validates_hash_mime_size_and_page_count() -> None:
    for overrides in (
        {"content_sha256": "A" * 64},
        {"content_sha256": "short"},
        {"mime_type": "application/octet-stream"},
        {"size_bytes": -1},
        {"page_count": 0},
    ):
        with pytest.raises(BundleSerializationError):
            identity(**overrides)


def test_metadata_absent_present_empty_and_populated_keep_contract() -> None:
    absent = json.loads(serialize_metadata(None))
    empty = json.loads(serialize_metadata(metadata(title=None, authors=None, publication_year=None, venue=None, doi=None, arxiv_id=None, citation_key=None)))
    populated = json.loads(serialize_metadata(metadata()))
    assert absent["state"] == "absent"
    assert empty["state"] == "present"
    assert all(empty[field] is None for field in ("title", "authors", "publication_year", "venue", "doi", "arxiv_id", "citation_key"))
    assert populated["state"] == "present"
    assert populated["title"] == "研究 {模型}"
    for value in (absent, empty, populated):
        assert "paper_id" not in value
        assert "updated_at" not in value
    assert "研究" in serialize_metadata(metadata()) .decode("utf-8")


def test_metadata_allows_titleless_paper_and_never_falls_back_to_filename() -> None:
    payload = json.loads(serialize_metadata(metadata(title=None, authors=["作者"])))
    assert payload["title"] is None
    assert "original_filename" not in json.dumps(payload)
    assert citation_bib_is_available(metadata(title=None)) is False
    assert citation_bib_is_available(metadata(title="  标题  ")) is True


def test_annotation_round_trip_fields_and_assistant_provenance() -> None:
    payload = json.loads(serialize_annotations([annotation()]))
    item = payload["annotations"][0]
    assert item["author"] == "assistant"
    assert item["remember"] is True
    assert item["thread_id"]
    assert item["normalized_quads"] == [{"x": .2, "y": .3, "width": .2, "height": .04}]
    assert "paper_id" not in item
    assert "session_id" not in item


def test_annotations_are_deterministic_when_input_order_is_shuffled() -> None:
    first = annotation(annotation_id="00000000-0000-0000-0000-000000000001", page=2, x=.4)
    second = annotation(annotation_id="00000000-0000-0000-0000-000000000002", page=1, x=.1)
    assert serialize_annotations([first, second]) == serialize_annotations([second, first])


def test_annotation_duplicate_and_invalid_geometry_are_rejected() -> None:
    duplicate = annotation(annotation_id="same")
    with pytest.raises(BundleSerializationError):
        serialize_annotations([duplicate, duplicate])
    invalid = annotation()
    invalid = Annotation(**{**invalid.__dict__, "normalized_quads": [{"x": 1.1, "y": .1, "width": .2, "height": .2}]})
    with pytest.raises(BundleSerializationError):
        serialize_annotations([invalid])


def test_annotation_non_object_quad_and_invalid_author_are_rejected() -> None:
    value = annotation()
    with pytest.raises(BundleSerializationError):
        serialize_annotations([Annotation(**{**value.__dict__, "normalized_quads": ["not-a-quad"]})])  # type: ignore[list-item]
    with pytest.raises(BundleSerializationError):
        serialize_annotations([Annotation(**{**value.__dict__, "author": "unknown"})])


def test_sticky_notes_preserve_coordinates_and_are_deterministic() -> None:
    first = sticky("00000000-0000-0000-0000-000000000001", page=2, x=.8)
    second = sticky("00000000-0000-0000-0000-000000000002", page=1, x=.2)
    payload = json.loads(serialize_sticky_notes([first, second]))
    assert payload["sticky_notes"][0]["page_number"] == 1
    assert payload["sticky_notes"][1]["normalized_x"] == .8
    assert serialize_sticky_notes([first, second]) == serialize_sticky_notes([second, first])
    assert "paper_id" not in serialize_sticky_notes([first]).decode("utf-8")


def test_sticky_duplicate_and_invalid_coordinates_are_rejected() -> None:
    with pytest.raises(BundleSerializationError):
        serialize_sticky_notes([sticky("same"), sticky("same")])
    invalid = sticky("invalid")
    invalid = StickyNote(**{**invalid.__dict__, "x": 1.1})
    with pytest.raises(BundleSerializationError):
        serialize_sticky_notes([invalid])


def test_summary_preserves_legacy_null_x_and_spatial_x() -> None:
    legacy = json.loads(serialize_summary_notes([summary("legacy", x=None)]))["summary_notes"][0]
    spatial = json.loads(serialize_summary_notes([summary("spatial", x=.73)]))["summary_notes"][0]
    assert legacy["normalized_x"] is None
    assert spatial["normalized_x"] == .73
    assert "paper_id" not in json.dumps(legacy)


def test_summary_duplicate_and_invalid_position_are_rejected() -> None:
    with pytest.raises(BundleSerializationError):
        serialize_summary_notes([summary("same"), summary("same")])
    invalid = summary("invalid", x=1.2)
    with pytest.raises(BundleSerializationError):
        serialize_summary_notes([invalid])


def test_summary_notes_are_deterministic_when_input_order_is_shuffled() -> None:
    first = summary("00000000-0000-0000-0000-000000000001", x=None, y=.2)
    second = summary("00000000-0000-0000-0000-000000000002", x=.8, y=.8)
    assert serialize_summary_notes([first, second]) == serialize_summary_notes([second, first])


def test_canonical_json_rejects_non_serializable_and_nan_values() -> None:
    with pytest.raises(BundleSerializationError):
        canonical_json_bytes({"value": object()})
    with pytest.raises(BundleSerializationError):
        canonical_json_bytes({"value": float("nan")})


def test_all_serializers_are_stable_and_deferred_data_is_not_emitted() -> None:
    outputs = [
        serialize_metadata(metadata()),
        serialize_annotations([annotation()]),
        serialize_sticky_notes([sticky()]),
        serialize_summary_notes([summary()]),
    ]
    for output in outputs:
        assert output == output.decode("utf-8").encode("utf-8")
        assert output.endswith(b"\n") and not output.endswith(b"\n\n")
        text = output.decode("utf-8")
        assert "vocabulary" not in text
        assert "reading_sessions" not in text
        assert "selection_id" not in text
