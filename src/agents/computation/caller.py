"""How Computation reaches the tax_math MCP server.

A ToolCaller is any function (tool_name, arguments) -> result dict, the same shape the
Review agent uses, so a test can pass a fake one and Review can replay what Computation did.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, Callable

ToolCaller = Callable[[str, dict[str, Any]], dict[str, Any]]


class ToolCallError(RuntimeError):
    """A tax_math tool refused its inputs (missing, malformed or contradictory)."""

    def __init__(self, tool: str, message: str) -> None:
        self.tool = tool
        super().__init__(f"{tool}: {message}")


def mcp_tool_caller() -> ToolCaller:
    """Call tax_math through a real MCP client session, in-process.

    In-process keeps it fast (about 20 ms a call, versus a new Python process per call over
    stdio) while still going through the MCP server and its schema validation. Each call runs
    in a worker thread, so it also works when LangGraph is already running an event loop.
    """
    from mcp import Client  # imported here so the rest of the agent imports without mcp
    from tax_math.server import build_server

    server = build_server()

    async def _call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with Client(server) as client:
            result = await client.call_tool(tool, arguments)
        if result.is_error:
            raise ToolCallError(tool, result.content[0].text if result.content else "unknown error")
        return dict(result.structured_content)

    def call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _call(tool, arguments)).result()

    return call
