"""How Review re-runs a tax_math tool call.

Review does not trust the numbers on the return. For every line the
Computation agent produced with a tool, Review calls the same tool again with
the inputs recorded in that line's provenance and compares the answer.

A ToolCaller is any function (tool_name, inputs) -> result dict that has a
"value" key. Tests pass a fake one; production uses the MCP server.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import sys
from pathlib import Path
from typing import Any, Callable

ToolCaller = Callable[[str, dict[str, Any]], dict[str, Any]]


def make_mcp_tool_caller(repo_root: str | Path) -> ToolCaller:
    """Call the tax_math MCP server (stdio), one short-lived server per call.

    A fresh server per call is slow-ish (a few hundred ms) but keeps this
    simple and safe: there is no session to leak between taxpayers. A return
    has about 15 tool-made lines, so a review costs a few seconds.

    The call runs in a worker thread so it also works when LangGraph is already
    running an event loop (asyncio.run cannot be nested).
    """
    root = str(repo_root)

    async def _call(tool: str, inputs: dict[str, Any]) -> dict[str, Any]:
        from mcp import Client  # imported here so tests without mcp still import this module
        from mcp.client.stdio import StdioServerParameters

        params = StdioServerParameters(command=sys.executable, args=["run.py", "tax-math-server"], cwd=root)
        async with Client(params) as client:
            result = await client.call_tool(tool, inputs)
            return dict(result.structured_content)

    def caller(tool: str, inputs: dict[str, Any]) -> dict[str, Any]:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(_call(tool, inputs))).result()

    return caller
