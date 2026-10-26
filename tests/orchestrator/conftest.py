"""Shared setup for the orchestrator tests. pytest loads this file automatically; it is not a
test itself.

It provides the full SilverFile graph, built with the offline W-2 reader (no API key) and the
real tax_math tools, so a test can ask for ``graph`` and get one ready to run.
"""

import pytest

from agents.computation import mcp_tool_caller
from agents.extraction import FixtureW2Extractor
from graph import build_graph
from memory import make_checkpointer


@pytest.fixture(scope="session")
def call():
    """The real tax_math tools, through the MCP server, in-process."""
    return mcp_tool_caller()


@pytest.fixture
def make_graph(call):
    """The full graph with the offline W-2 reader, so no API key is needed."""

    def make(checkpointer=None, **options):
        options = {"extractor": FixtureW2Extractor(), "computation_caller": call, "review_caller": call,
                   **options}
        return build_graph(checkpointer or make_checkpointer(), **options)

    return make


@pytest.fixture
def graph(make_graph):
    return make_graph()
