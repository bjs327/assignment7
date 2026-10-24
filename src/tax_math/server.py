"""MCP server exposing tax_math to the Computation agent over stdio.

Run from the repo root:  python run.py tax-math-server
"""

import functools
from collections.abc import Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import TOOLS, __version__
from .models import CalcResult, TaxMathInputError

INSTRUCTIONS = (
    "Deterministic arithmetic for the 2025 Form 1040-SR. Every number on the draft return "
    "must come from one of these tools. Each result has a whole-dollar `value` for the form, "
    "plus `steps` (the working), `citations` (rule IDs) and `inputs` for the provenance record. "
    "An error means an input is missing, malformed or contradictory: route it back to the "
    "agent that supplied it rather than retrying with a guess."
)

# Pure functions: same inputs, same answer, no side effects.
_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def _as_tool(fn: Callable[..., CalcResult]) -> Callable[..., CalcResult]:
    """Report bad input as an expected tool error rather than a server crash."""

    @functools.wraps(fn)
    def tool(*args, **kwargs) -> CalcResult:
        try:
            return fn(*args, **kwargs)
        except TaxMathInputError as exc:
            raise ToolError(str(exc)) from exc

    return tool


def build_server() -> MCPServer:
    server = MCPServer("tax_math", instructions=INSTRUCTIONS, version=__version__)
    for fn in TOOLS:
        server.add_tool(_as_tool(fn), annotations=_ANNOTATIONS)
    return server


def main() -> None:
    build_server().run()  # stdio


if __name__ == "__main__":
    main()
