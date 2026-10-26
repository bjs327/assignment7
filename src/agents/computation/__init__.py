"""Computation: fills in every derived line of the draft return from tax_math tool calls.

Owner: Brendan. See agent.py for the node and its routing, inputs.py for where each number
comes from, and docs/IMPLEMENTATION_NOTES.md for the design.
"""

from .agent import NODE_NAME, ComputationState, compute_return, make_computation_node, run_computation
from .caller import ToolCaller, ToolCallError, mcp_tool_caller
from .inputs import ENTERED_LINES, MissingInput, gather

__all__ = [
    "ENTERED_LINES",
    "NODE_NAME",
    "ComputationState",
    "MissingInput",
    "ToolCallError",
    "ToolCaller",
    "compute_return",
    "gather",
    "make_computation_node",
    "mcp_tool_caller",
    "run_computation",
]
