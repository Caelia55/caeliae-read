from __future__ import annotations

import asyncio
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
            tools = await session.list_tools()
            for tool in tools.tools:
                print(tool.name)


if __name__ == "__main__":
    asyncio.run(main())
