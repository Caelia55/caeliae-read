from __future__ import annotations

import asyncio
import base64
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def tool_payload(result: object) -> dict[str, object]:
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured.get("result", structured)
    return json.loads(getattr(result, "content")[0].text)


async def main() -> None:
    request = json.loads(base64.b64decode(sys.stdin.read().strip()).decode("utf-8"))
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "caeliae_read.mcp.server"],
        env=os.environ.copy(),
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            context = tool_payload(await session.call_tool("get_current_reading_context", {}))
            anchor = request["anchor"]
            if context["paper"]["paper_id"] != anchor["paper_id"] or context["page_number"] != anchor["page_number"]:
                raise RuntimeError("browser reading context and requested anchor do not match")
            payload = {
                "paper_id": context["paper"]["paper_id"],
                "session_id": context["session_id"],
                "page_number": context["page_number"],
                "exact_text": anchor["exact_text"],
                "prefix": anchor["prefix"],
                "suffix": anchor["suffix"],
                "normalized_quads": anchor["normalized_quads"],
                "page_width": anchor["page_width"],
                "page_height": anchor["page_height"],
                "rotation": anchor["rotation"],
                "note": request["note"],
                "remember": True,
                "idempotency_key": request["idempotency_key"],
            }
            first = tool_payload(await session.call_tool("create_assistant_annotation", {"payload": payload}))
            replay = tool_payload(await session.call_tool("create_assistant_annotation", {"payload": payload}))
            listed = tool_payload(await session.call_tool("list_annotations", {"page_number": context["page_number"]}))
            annotation_id = first["annotation"]["annotation_id"]
            matches = [item for item in listed["annotations"] if item["annotation_id"] == annotation_id]
            result = json.dumps({"context": context, "payload": payload, "first": first, "replay": replay, "persisted_matches": matches}, ensure_ascii=False).encode("utf-8")
            print(base64.b64encode(result).decode("ascii"))


if __name__ == "__main__":
    asyncio.run(main())
