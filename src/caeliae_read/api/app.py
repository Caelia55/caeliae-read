from __future__ import annotations

from collections.abc import AsyncIterator
import csv
import io
import json
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from caeliae_read import __version__
from caeliae_read.api.schemas import ReadingDraftInput, ReadingFeedbackInput, ReadingStateInput, ReadingStateSaved, ReadingSubmitInput, ReadingTaskCreateInput, SummaryNoteCreateInput, SummaryNoteUpdateInput, VocabularyFromSelectionInput, VocabularyInput, VocabularyReviewInput, VocabularyUpdateInput
from caeliae_read.application.service import (
    CheckpointConflictError,
    CaeliaeReadError,
    CaeliaeReadService,
    EventConflictError,
    PaperNotFoundError,
    ReadingConflictError,
    ReadingNotFoundError,
    ReadingValidationError,
    SessionNotFoundError,
    ValidationError,
    get_service,
    require_uuid,
)
from caeliae_read.config import PROJECT_ROOT, get_settings


def problem(error: CaeliaeReadError) -> HTTPException:
    if isinstance(error, (EventConflictError, CheckpointConflictError, ReadingConflictError)):
        code = status.HTTP_409_CONFLICT
    elif isinstance(error, (PaperNotFoundError, SessionNotFoundError, ReadingNotFoundError)):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, ReadingValidationError):
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
    else:
        code = status.HTTP_400_BAD_REQUEST
    detail: dict[str, object] = {"code": error.code, "message": str(error)}
    if isinstance(error, CheckpointConflictError):
        detail.update(
            current_revision=error.current_revision,
            received_revision=error.received_revision,
        )
    if isinstance(error, ReadingConflictError):
        if error.current_revision is not None:
            detail.update(
                current_revision=error.current_revision,
                received_revision=error.received_revision,
            )
    return HTTPException(status_code=code, detail=detail)


def create_app(service: CaeliaeReadService | None = None) -> FastAPI:
    settings = get_settings()
    read_service = service or get_service()
    app = FastAPI(title="Caeliae Read", version=__version__, docs_url="/api/docs")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "caeliae-read", "version": __version__}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        return {"status": "ready", "service": "caeliae-read", "version": __version__}

    @app.get("/api/papers")
    async def list_papers(limit: int = 50) -> dict[str, object]:
        try:
            return {"papers": read_service.list_papers(limit)}
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.post("/api/papers", status_code=status.HTTP_201_CREATED)
    async def upload_paper(file: UploadFile = File(...)) -> dict[str, object]:
        async def chunks() -> AsyncIterator[bytes]:
            while chunk := await file.read(64 * 1024):
                yield chunk

        try:
            paper, deduplicated = await read_service.upload_pdf(
                original_filename=file.filename or "",
                content_type=file.content_type or "",
                chunks=chunks(),
            )
        except CaeliaeReadError as error:
            raise problem(error) from error
        finally:
            await file.close()
        return {"paper": paper.public_dict(), "deduplicated": deduplicated}

    @app.get("/api/papers/{paper_id}")
    async def get_paper(paper_id: str) -> dict[str, object]:
        try:
            return {"paper": read_service.get_paper(paper_id).public_dict()}
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.get("/api/papers/{paper_id}/file")
    async def get_paper_file(paper_id: str) -> FileResponse:
        try:
            paper, path = read_service.get_paper_file(paper_id)
        except CaeliaeReadError as error:
            raise problem(error) from error
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=paper.original_filename,
            content_disposition_type="inline",
        )

    @app.post("/api/papers/{paper_id}/sessions", status_code=status.HTTP_201_CREATED)
    async def create_session(paper_id: str) -> dict[str, str]:
        try:
            return read_service.create_session(paper_id)
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.put("/api/sessions/{session_id}/state", response_model=ReadingStateSaved)
    async def save_state(session_id: str, payload: ReadingStateInput) -> dict[str, object]:
        data = payload.model_dump(mode="json")
        if data["session_id"] != session_id:
            raise problem(ValidationError("path session_id must match request body"))
        try:
            return read_service.save_reading_state(data)
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.get("/api/sessions/{session_id}/context")
    async def get_context(session_id: str) -> dict[str, object]:
        try:
            return read_service.get_reading_context(session_id).public_dict()
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.get("/api/reading/current")
    async def get_current_context() -> dict[str, object] | None:
        context = read_service.get_current_reading_context()
        return context.public_dict() if context else None

    @app.get("/api/reading/tasks")
    async def list_reading_tasks(status: str | None = None) -> dict[str, object]:
        try:
            return {"tasks": read_service.list_reading_tasks(status)}
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.post("/api/reading/tasks", status_code=status.HTTP_201_CREATED)
    async def create_reading_task(payload: ReadingTaskCreateInput) -> dict[str, object]:
        try:
            return read_service.create_reading_task(payload.model_dump(mode="json"))
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.get("/api/reading/tasks/{task_id}")
    async def get_reading_task(task_id: str) -> dict[str, object]:
        try:
            return read_service.get_reading_task(task_id)
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.post("/api/reading/tasks/{task_id}/open")
    async def open_reading_task(task_id: str) -> dict[str, object]:
        try:
            return read_service.open_reading_task(task_id)
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.post("/api/reading/tasks/{task_id}/archive")
    async def archive_reading_task(task_id: str) -> dict[str, object]:
        try:
            return read_service.archive_reading_task(task_id)
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.get("/api/reading/tasks/{task_id}/submission")
    async def get_reading_submission(task_id: str) -> dict[str, object]:
        try:
            return read_service.get_reading_submission(task_id)
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.put("/api/reading/tasks/{task_id}/submission")
    async def save_reading_draft(task_id: str, payload: ReadingDraftInput) -> dict[str, object]:
        try:
            return read_service.save_reading_draft(task_id, payload.model_dump(mode="json"))
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.post("/api/reading/tasks/{task_id}/submission/submit")
    async def submit_reading(task_id: str, payload: ReadingSubmitInput) -> dict[str, object]:
        try:
            return read_service.submit_reading(task_id, payload.model_dump(mode="json"))
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.get("/api/reading/tasks/{task_id}/feedback")
    async def list_reading_feedback(task_id: str) -> dict[str, object]:
        try:
            return read_service.list_reading_feedback(task_id)
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.post("/api/reading/tasks/{task_id}/feedback", status_code=status.HTTP_201_CREATED)
    async def add_reading_feedback(task_id: str, payload: ReadingFeedbackInput) -> dict[str, object]:
        try:
            return read_service.add_reading_feedback(task_id, payload.model_dump(mode="json"))
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.get("/api/annotations")
    async def list_annotations(paper_id: str | None = None, page_number: int | None = None) -> dict[str, object]:
        try:
            return {"annotations": read_service.list_annotations(paper_id, page_number)}
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.post("/api/annotations", status_code=status.HTTP_201_CREATED)
    async def create_annotation(payload: dict[str, object]) -> dict[str, object]:
        try:
            return read_service.create_assistant_annotation(payload)
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.post("/api/annotations/user", status_code=status.HTTP_201_CREATED)
    async def create_user_annotation(payload: dict[str, object]) -> dict[str, object]:
        try: return read_service.create_user_annotation(payload)
        except CaeliaeReadError as error: raise problem(error) from error

    @app.put("/api/annotations/{annotation_id}")
    async def update_user_annotation(annotation_id: str, payload: dict[str, object]) -> dict[str, object]:
        try: return read_service.update_user_annotation(annotation_id, payload)
        except CaeliaeReadError as error: raise problem(error) from error

    @app.post("/api/annotations/{annotation_id}/remember/revoke")
    async def revoke_assistant_remember(annotation_id: str) -> dict[str, object]:
        try:
            return read_service.revoke_assistant_remember(annotation_id)
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.delete("/api/annotations/{annotation_id}")
    async def delete_user_annotation(annotation_id: str, session_id: str) -> dict[str, bool]:
        try: return {"deleted": read_service.repository.delete_user_annotation(annotation_id, require_uuid(session_id, "session_id"))}
        except CaeliaeReadError as error: raise problem(error) from error

    @app.get("/api/vocabulary")
    async def list_vocabulary(search: str | None = None, paper_id: str | None = None) -> dict[str, object]:
        try:
            return {"entries": read_service.list_vocabulary(search, paper_id)}
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.post("/api/vocabulary", status_code=status.HTTP_201_CREATED)
    async def create_vocabulary(payload: VocabularyInput) -> dict[str, object]:
        try:
            return read_service.create_vocabulary(payload.model_dump(mode="json"))
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.post("/api/vocabulary/from-selection", status_code=status.HTTP_201_CREATED)
    async def create_vocabulary_from_selection(payload: VocabularyFromSelectionInput) -> dict[str, object]:
        try:
            data = payload.model_dump(mode="json")
            selection = data.pop("selection")
            return read_service.create_vocabulary(data, selection)
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.put("/api/vocabulary/{vocabulary_id}")
    async def update_vocabulary(vocabulary_id: str, payload: VocabularyUpdateInput) -> dict[str, object]:
        try:
            return read_service.update_vocabulary(vocabulary_id, payload.model_dump(mode="json"))
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.delete("/api/vocabulary/{vocabulary_id}")
    async def delete_vocabulary(vocabulary_id: str) -> dict[str, bool]:
        try:
            return {"deleted": read_service.delete_vocabulary(vocabulary_id)}
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.get("/api/vocabulary/export")
    async def export_vocabulary(format: str = "json", search: str | None = None, paper_id: str | None = None) -> Response:
        try:
            entries = read_service.export_vocabulary(search, paper_id)
        except CaeliaeReadError as error:
            raise problem(error) from error
        if format == "json":
            return Response(json.dumps({"entries": entries}, ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": "attachment; filename=caeliae-vocabulary.json"})
        if format != "csv":
            raise problem(ValidationError("format must be json or csv"))
        output = io.StringIO()
        fields = ["vocabulary_id", "term", "definition", "part_of_speech", "pronunciation", "examples", "notes", "tags", "source_paper_id", "source_session_id", "source_page_number", "created_at", "updated_at"]
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for entry in entries:
            writer.writerow({**entry, "examples": " | ".join(entry["examples"]), "tags": " | ".join(entry["tags"])})
        return Response(output.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=caeliae-vocabulary.csv"})

    @app.post("/api/vocabulary/reviews", status_code=status.HTTP_201_CREATED)
    async def record_vocabulary_review(payload: VocabularyReviewInput) -> dict[str, object]:
        try:
            return read_service.record_vocabulary_review(payload.model_dump(mode="json"))
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.get("/api/sticky-notes")
    async def list_sticky_notes(paper_id: str | None = None, page: int | None = None) -> dict[str, object]:
        try:
            return {"sticky_notes": read_service.list_sticky_notes(paper_id, page)}
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.post("/api/sticky-notes", status_code=status.HTTP_201_CREATED)
    async def create_sticky_note(payload: dict[str, object]) -> dict[str, object]:
        try:
            return read_service.create_sticky_note(payload)
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.put("/api/sticky-notes/{note_id}")
    async def update_sticky_note(note_id: str, payload: dict[str, object]) -> dict[str, object]:
        try:
            return read_service.update_sticky_note(note_id, payload)
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.delete("/api/sticky-notes/{note_id}")
    async def delete_sticky_note(note_id: str) -> dict[str, bool]:
        try:
            return {"deleted": read_service.delete_sticky_note(note_id)}
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.get("/api/summary-notes")
    async def list_summary_notes(paper_id: str, page_number: int | None = None) -> dict[str, object]:
        try:
            return {"summary_notes": read_service.list_summary_notes(paper_id, page_number)}
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.post("/api/summary-notes", status_code=status.HTTP_201_CREATED)
    async def create_summary_note(payload: SummaryNoteCreateInput) -> dict[str, object]:
        try:
            return read_service.create_summary_note(payload.model_dump(mode="json"))
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.put("/api/summary-notes/{summary_note_id}")
    async def update_summary_note(summary_note_id: str, payload: SummaryNoteUpdateInput) -> dict[str, object]:
        try:
            return read_service.update_summary_note(summary_note_id, payload.model_dump(mode="json"))
        except CaeliaeReadError as error:
            raise problem(error) from error

    @app.delete("/api/summary-notes/{summary_note_id}")
    async def delete_summary_note(summary_note_id: str) -> dict[str, bool]:
        try:
            return {"deleted": read_service.delete_summary_note(summary_note_id)}
        except CaeliaeReadError as error:
            raise problem(error) from error

    web_dist = PROJECT_ROOT / "web" / "dist"
    if web_dist.is_dir():
        app.mount("/reader", StaticFiles(directory=web_dist, html=True), name="reader")

        @app.get("/", include_in_schema=False)
        async def root() -> RedirectResponse:
            return RedirectResponse("/reader/")
    else:
        @app.get("/", include_in_schema=False)
        async def root_without_build() -> dict[str, str]:
            return {"reader": "Run the Vite development server from web/ on port 5174."}

    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "caeliae_read.api.app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )
