from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


EXPECTED = [
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
WRITE_TOOLS = {"create_assistant_annotation", "create_reading_task", "create_reading_feedback"}
CALLABLE_WITHOUT_INPUT = {
    "list_papers",
    "get_current_reading_context",
    "get_current_selection",
    "get_checkpoint",
    "list_annotations",
    "list_reading_tasks",
}
FORBIDDEN_KEYS = {"content_sha256", "storage_key", "absolute_path", "file_path"}


def scan(value: object) -> None:
    if isinstance(value, dict):
        assert not (FORBIDDEN_KEYS & set(value))
        for nested in value.values():
            scan(nested)
    elif isinstance(value, list):
        for nested in value:
            scan(nested)
    elif isinstance(value, str):
        lowered = value.lower()
        assert "\\users\\" not in lowered
        assert not (len(value) > 2 and value[1:3] == ":\\")


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    temporary = tempfile.TemporaryDirectory(prefix="caeliae-read-audit-")
    env = os.environ.copy()
    env.pop("COREAD_DATA_ROOT", None)
    env["CAELIAE_READ_DATA_ROOT"] = temporary.name
    parameters = StdioServerParameters(
        command="cmd.exe",
        args=["/d", "/c", str(root / "start-caeliae-read-mcp.cmd")],
        env=env,
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = [tool.name for tool in listed.tools]
            assert names == EXPECTED
            for name in EXPECTED:
                tool = next(tool for tool in listed.tools if tool.name == name)
                assert tool.annotations.readOnlyHint is (name not in WRITE_TOOLS)
                assert tool.inputSchema and tool.outputSchema
                if name not in CALLABLE_WITHOUT_INPUT:
                    continue
                result = await session.call_tool(name, {})
                payload = result.structuredContent
                if payload is None:
                    payload = json.loads(result.content[0].text) if result.content else None
                scan(payload)
            print(json.dumps({"tools": names, "safe_output_audit": "passed"}))
    temporary.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
