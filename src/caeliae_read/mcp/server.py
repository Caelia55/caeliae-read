from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from caeliae_read.application.service import CaeliaeReadService, get_service
from caeliae_read.config import get_settings
from caeliae_read.domain.public_schemas import (
    CheckpointOutput,
    PaperListOutput,
    ReadingMcpFeedbackCreatedOutput,
    ReadingMcpQuestionInput,
    ReadingMcpSubmissionOutput,
    ReadingMcpTaskCreatedOutput,
    ReadingMcpTaskListOutput,
    ReadingMcpTaskOutput,
    ReadingContextOutput,
    SelectionOutput,
)


READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

mcp = FastMCP(
    "caeliae-read",
    instructions=(
        "Read the user's locally stored paper list and current reading state. "
        "Eight tools are read-only; create_assistant_annotation, create_reading_task, and "
        "create_reading_feedback perform scoped, idempotent writes. Page text is bounded; "
        "no tool returns a full PDF."
    ),
)


def service() -> CaeliaeReadService:
    return get_service()


@mcp.tool(
    description=(
        "List compact metadata for locally uploaded papers. Does not return file paths, "
        "file content, or full-paper text."
    ),
    annotations=READ_ONLY,
)
def list_papers(
    limit: Annotated[int, Field(ge=1, le=100, description="Maximum papers to return")] = 50,
) -> PaperListOutput:
    return PaperListOutput.model_validate({"papers": service().list_papers(limit)})


@mcp.tool(
    description=(
        "Return the most recently updated reading session: paper metadata, current page, "
        "bounded current-page text, checkpoint, and current selection. Returns null when no state exists."
    ),
    annotations=READ_ONLY,
)
def get_current_reading_context() -> ReadingContextOutput | None:
    context = service().get_current_reading_context()
    return ReadingContextOutput.model_validate(context.public_dict()) if context else None


@mcp.tool(
    description=(
        "Return the current text selection and normalized page-relative quads from the "
        "most recently updated reading session, or null when nothing is selected."
    ),
    annotations=READ_ONLY,
)
def get_current_selection() -> SelectionOutput | None:
    selection = service().get_current_selection()
    return SelectionOutput.model_validate(selection.public_dict()) if selection else None


@mcp.tool(
    description=(
        "Return the current paper, session, page number, monotonic revision, and update time, "
        "or null when no checkpoint exists."
    ),
    annotations=READ_ONLY,
)
def get_checkpoint() -> CheckpointOutput | None:
    checkpoint = service().get_checkpoint()
    return CheckpointOutput.model_validate(checkpoint.public_dict()) if checkpoint else None


@mcp.tool(
    description="List annotations and discussion notes for the current paper or page. Read-only.",
    annotations=READ_ONLY,
)
def list_annotations(page_number: int | None = None) -> dict[str, object]:
    return {"annotations": service().list_annotations(page_number=page_number)}


@mcp.tool(
    description="Create an assistant-authored annotation or reply on the current selection. Idempotent and limited to assistant authorship.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def create_assistant_annotation(payload: dict) -> dict[str, object]:
    return service().create_assistant_annotation(payload)


@mcp.tool(
    description="List Reading tasks and lifecycle status. Read-only; does not open a Reader session or change current context.",
    annotations=READ_ONLY,
)
def list_reading_tasks(
    status: Literal["draft", "active", "archived"] | None = None,
) -> ReadingMcpTaskListOutput:
    return ReadingMcpTaskListOutput.model_validate({"tasks": service().list_agent_reading_tasks(status)})


@mcp.tool(
    description="Read one Reading task, its existing paper metadata, and questions. Read-only; reference answers are visible and no score is calculated.",
    annotations=READ_ONLY,
)
def get_reading_task(task_id: UUID) -> ReadingMcpTaskOutput:
    return ReadingMcpTaskOutput.model_validate(service().get_agent_reading_task(str(task_id)))


@mcp.tool(
    description="Create an active Reading task for an existing paper with Agent origin. The tool cannot upload files or create papers.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def create_reading_task(
    paper_id: UUID,
    title: Annotated[str, Field(min_length=1, max_length=200)],
    questions: Annotated[list[ReadingMcpQuestionInput], Field(min_length=1, max_length=100)],
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)],
    instructions: Annotated[str, Field(max_length=4_000)] = "",
    due_at: Annotated[str | None, Field(max_length=64)] = None,
    origin_ref: Annotated[str | None, Field(max_length=200)] = None,
) -> ReadingMcpTaskCreatedOutput:
    payload = {
        "paper_id": str(paper_id),
        "title": title,
        "instructions": instructions,
        "questions": [question.model_dump(mode="json") for question in questions],
        "due_at": due_at,
        "origin_ref": origin_ref,
        "idempotency_key": idempotency_key,
    }
    return ReadingMcpTaskCreatedOutput.model_validate(service().create_agent_reading_task(payload))


@mcp.tool(
    description="Inspect a user's Reading submission, answers, revision, and feedback history. Read-only; never mutates answers.",
    annotations=READ_ONLY,
)
def get_reading_submission(task_id: UUID) -> ReadingMcpSubmissionOutput:
    return ReadingMcpSubmissionOutput.model_validate(service().get_agent_reading_submission(str(task_id)))


@mcp.tool(
    description="Append Agent feedback to a submitted or reviewed Reading submission. It cannot submit or change user answers.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def create_reading_feedback(
    task_id: UUID,
    submission_id: UUID,
    expected_submission_revision: Annotated[int, Field(ge=1)],
    feedback_text: Annotated[str, Field(min_length=1, max_length=4_000)],
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)],
    question_feedback: dict[str, Annotated[str, Field(max_length=2_000)]] | None = None,
) -> ReadingMcpFeedbackCreatedOutput:
    result = service().create_agent_reading_feedback(
        str(task_id),
        submission_id=str(submission_id),
        expected_submission_revision=expected_submission_revision,
        feedback_text=feedback_text,
        question_feedback=question_feedback,
        idempotency_key=idempotency_key,
    )
    return ReadingMcpFeedbackCreatedOutput.model_validate(result)


def main() -> None:
    # Fail unsafe configuration before accepting protocol requests; no DB is opened.
    get_settings()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
