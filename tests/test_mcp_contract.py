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

from conftest import state_payload
from caeliae_read.application.service import CaeliaeReadService


EXPECTED_TOOLS = {
    "list_papers",
    "get_current_reading_context",
    "get_current_selection",
    "get_checkpoint",
    "list_annotations",
    "create_assistant_annotation",
    "list_reading_tasks",
    "get_reading_task",
    "create_reading_task",
    "get_reading_submission",
    "create_reading_feedback",
}

EXPECTED_TOOL_ORDER = [
    "list_papers",
    "get_current_reading_context",
    "get_current_selection",
    "get_checkpoint",
    "list_annotations",
    "create_assistant_annotation",
    "list_reading_tasks",
    "get_reading_task",
    "create_reading_task",
    "get_reading_submission",
    "create_reading_feedback",
]


async def inspect_stdio(data_root: Path, call: str | None = None) -> tuple[object, object | None]:
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
            await session.initialize()
            listed = await session.list_tools()
            called = await session.call_tool(call, {}) if call else None
            return listed, called


async def inspect_legacy_launcher(data_root: Path) -> object:
    env = os.environ.copy()
    env.pop("COREAD_DATA_ROOT", None)
    env["CAELIAE_READ_DATA_ROOT"] = str(data_root)
    root = Path(__file__).resolve().parents[1]
    parameters = StdioServerParameters(
        command="cmd.exe",
        args=["/d", "/c", str(root / "start-coread-mcp.cmd")],
        env=env,
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            return await session.list_tools()


def tool_payload(result: object) -> object:
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured.get("result", structured)
    content = getattr(result, "content")
    return json.loads(content[0].text)


def test_real_stdio_tools_list_contract(settings) -> None:
    listed, _ = asyncio.run(inspect_stdio(settings.data_root))
    tools = {tool.name: tool for tool in listed.tools}
    assert set(tools) == EXPECTED_TOOLS
    for tool in tools.values():
        if tool.name in {"create_assistant_annotation", "create_reading_task", "create_reading_feedback"}:
            assert tool.annotations.readOnlyHint is False
            assert tool.annotations.destructiveHint is False
        else:
            assert tool.annotations.readOnlyHint is True
            assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.openWorldHint is False
        json.dumps(tool.inputSchema)
        assert tool.outputSchema is not None
        json.dumps(tool.outputSchema)
        assert "additionalProperties" in json.dumps(tool.outputSchema)
    assert set(tools["list_papers"].inputSchema["properties"]) == {"limit"}
    for name in EXPECTED_TOOLS - {"list_papers", "create_assistant_annotation"}:
        if name in {"create_reading_task", "create_reading_feedback"}:
            assert tools[name].inputSchema.get("properties")
        elif name == "list_reading_tasks":
            assert set(tools[name].inputSchema.get("properties", {})) == {"status"}
        elif name in {"get_reading_task", "get_reading_submission"}:
            assert set(tools[name].inputSchema.get("properties", {})) == {"task_id"}
        elif name == "list_annotations":
            assert set(tools[name].inputSchema.get("properties", {})) == {"page_number"}
        else:
            assert tools[name].inputSchema.get("properties", {}) == {}


def test_legacy_launcher_stdio_lists_the_same_six_tools(settings) -> None:
    listed = asyncio.run(inspect_legacy_launcher(settings.data_root))
    assert [tool.name for tool in listed.tools] == EXPECTED_TOOL_ORDER


def test_http_and_real_stdio_mcp_share_identical_state(
    client: TestClient,
    service: CaeliaeReadService,
    settings,
    session: dict[str, str],
) -> None:
    payload = state_payload(session, event_id=str(uuid4()))
    response = client.put(f"/api/sessions/{session['session_id']}/state", json=payload)
    assert response.status_code == 200
    expected = client.get("/api/reading/current").json()

    _, result = asyncio.run(inspect_stdio(settings.data_root, "get_current_reading_context"))
    assert tool_payload(result) == expected
    assert "content_sha256" not in json.dumps(expected)
    assert "storage_key" not in json.dumps(expected)
