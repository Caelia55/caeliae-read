from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def tool_payload(result: object) -> dict[str, object]:
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured.get("result", structured)
    content = getattr(result, "content")
    try:
        return json.loads(content[0].text)
    except json.JSONDecodeError as error:
        raise AssertionError(content[0].text) from error


async def run_reading_mcp(data_root: Path, paper_id: str, client: TestClient) -> None:
    env = os.environ.copy()
    env.pop("COREAD_DATA_ROOT", None)
    env["CAELIAE_READ_DATA_ROOT"] = str(data_root)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "caeliae_read.mcp.server"],
        env=env,
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "caeliae-read"
            listed = await session.list_tools()
            assert len(listed.tools) == 11

            empty = await session.call_tool("list_reading_tasks", {})
            assert tool_payload(empty) == {"tasks": []}

            invalid_task = await session.call_tool("get_reading_task", {"task_id": str(uuid4())})
            assert invalid_task.isError

            question = {
                "ordinal": 1,
                "question_type": "short_text",
                "prompt": "What is the central claim?",
                "reference_answer": "The paper presents a bounded claim.",
            }
            create_args = {
                "paper_id": paper_id,
                "title": "Agent Reading task",
                "instructions": "Answer from the paper.",
                "questions": [question],
                "due_at": None,
                "origin_ref": "agent-run-1",
                "idempotency_key": "reading-task-create-1",
            }
            created_result = await session.call_tool("create_reading_task", create_args)
            created = tool_payload(created_result)
            assert created["replayed"] is False
            task = created["task"]
            assert task["origin_kind"] == "agent"
            assert task["status"] == "active"
            create_schema = next(tool for tool in listed.tools if tool.name == "create_reading_task").inputSchema
            feedback_schema = next(tool for tool in listed.tools if tool.name == "create_reading_feedback").inputSchema
            assert "origin_kind" not in create_schema["properties"]
            assert "status" not in create_schema["properties"]
            assert "source_kind" not in feedback_schema["properties"]

            replayed = await session.call_tool("create_reading_task", create_args)
            assert tool_payload(replayed)["replayed"] is True
            conflict_args = {**create_args, "title": "Different task"}
            conflict = await session.call_tool("create_reading_task", conflict_args)
            assert conflict.isError

            task_id = task["task_id"]
            detail = tool_payload(await session.call_tool("get_reading_task", {"task_id": task_id}))
            assert detail["task"]["origin_kind"] == "agent"
            assert detail["questions"][0]["reference_answer"] == question["reference_answer"]

            before_submission = tool_payload(await session.call_tool("get_reading_submission", {"task_id": task_id}))
            assert before_submission["submission"] is None
            assert before_submission["feedback_history"] == []
            assert before_submission["latest_feedback"] is None

            question_id = detail["questions"][0]["question_id"]
            draft = client.put(
                f"/api/reading/tasks/{task_id}/submission",
                json={
                    "revision": 0,
                    "answers": [{"question_id": question_id, "answer_text": "The bounded claim."}],
                    "client_event_id": str(uuid4()),
                },
            )
            assert draft.status_code == 200
            submission_id = draft.json()["submission"]["submission_id"]
            submit = client.post(
                f"/api/reading/tasks/{task_id}/submission/submit",
                json={"revision": 1, "client_event_id": str(uuid4())},
            )
            assert submit.status_code == 200
            assert submit.json()["submission"]["status"] == "submitted"
            submission_revision = submit.json()["submission"]["revision"]

            submitted = tool_payload(await session.call_tool("get_reading_submission", {"task_id": task_id}))
            assert submitted["submission"]["submission_id"] == submission_id
            assert submitted["submission"]["revision"] == submission_revision
            assert submitted["submission"]["answers"][0]["answer_text"] == "The bounded claim."

            feedback_args = {
                "task_id": task_id,
                "submission_id": submission_id,
                "expected_submission_revision": submission_revision,
                "feedback_text": "The answer identifies the claim clearly.",
                "question_feedback": {question_id: "Good evidence selection."},
                "idempotency_key": "reading-feedback-1",
            }
            first_feedback = tool_payload(await session.call_tool("create_reading_feedback", feedback_args))
            assert first_feedback["feedback"]["revision"] == 1
            assert first_feedback["feedback"]["source_kind"] == "agent"
            assert first_feedback["submission_status"] == "reviewed"

            feedback_replay = tool_payload(await session.call_tool("create_reading_feedback", feedback_args))
            assert feedback_replay["replayed"] is True
            assert feedback_replay["feedback"]["feedback_id"] == first_feedback["feedback"]["feedback_id"]

            invalid_question = {
                **feedback_args,
                "idempotency_key": "reading-feedback-invalid-question",
                "question_feedback": {str(uuid4()): "wrong task"},
            }
            assert (await session.call_tool("create_reading_feedback", invalid_question)).isError

            second_feedback = tool_payload(
                await session.call_tool(
                    "create_reading_feedback",
                    {**feedback_args, "idempotency_key": "reading-feedback-2", "feedback_text": "A second explanation."},
                )
            )
            assert second_feedback["feedback"]["revision"] == 2

            reviewed = tool_payload(await session.call_tool("get_reading_submission", {"task_id": task_id}))
            assert [item["revision"] for item in reviewed["feedback_history"]] == [1, 2]
            assert reviewed["latest_feedback"]["revision"] == 2
            assert reviewed["submission"]["revision"] == submission_revision
            assert reviewed["submission"]["answers"][0]["answer_text"] == "The bounded claim."

            stale = await session.call_tool(
                "create_reading_feedback",
                {**feedback_args, "idempotency_key": "reading-feedback-stale", "expected_submission_revision": submission_revision - 1},
            )
            assert stale.isError

            mismatch = await session.call_tool(
                "create_reading_feedback",
                {**feedback_args, "idempotency_key": "reading-feedback-mismatch", "submission_id": str(uuid4())},
            )
            assert mismatch.isError

            archived = client.post(f"/api/reading/tasks/{task_id}/archive")
            assert archived.status_code == 200
            archived_new_key = await session.call_tool(
                "create_reading_feedback",
                {**feedback_args, "idempotency_key": "reading-feedback-after-archive"},
            )
            assert archived_new_key.isError
            archived_replay = await session.call_tool("create_reading_feedback", feedback_args)
            assert not archived_replay.isError
            assert tool_payload(archived_replay)["replayed"] is True


def test_reading_agent_mcp_capabilities(client: TestClient, settings, uploaded: dict[str, object]) -> None:
    asyncio.run(run_reading_mcp(settings.data_root, str(uploaded["paper_id"]), client))
