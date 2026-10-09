from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "caeliae_read.mcp.server"],
        env=os.environ.copy(),
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            result = await session.call_tool("get_current_reading_context", {})
            payload = result.structuredContent
            if payload is None:
                payload = json.loads(result.content[0].text)
            payload = payload.get("result", payload)
            selection = payload.get("selection")
            assert payload["page_number"] == 2
            assert selection["exact_text"] == "Selected passage for cross-client verification."
            print(json.dumps({
                "tool": "get_current_reading_context",
                "page_number": payload["page_number"],
                "selection": selection["exact_text"],
                "checkpoint_revision": payload["checkpoint"]["revision"],
            }))


if __name__ == "__main__":
    asyncio.run(main())
