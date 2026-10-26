"""Builds the LangGraph graph: the orchestrator's conditional routing, the parallel
extraction branch and its aggregation step, the bounded review loop, and the loop guard
(specification section 4.1).

Owner: Verena.

Hub and spoke. START goes to the orchestrator; every agent, and the human review step, goes
back to it; only the orchestrator's conditional edge chooses the next node.

    START -> orchestrator -> intake | extraction | eligibility_rules | computation | review
                             | human_review | finalize -> END

Extraction fans out one extract_document branch per W-2 (LangGraph Send), and
aggregate_extractions runs once they have all finished. The reducers in state.py make the
merged result the same whatever order the branches finish in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from agents.computation import make_computation_node
from agents.eligibility import eligibility_node
from agents.eligibility.agent import NODE_NAME as ELIGIBILITY_NODE
from agents.extraction import LLMVisionW2Extractor, aggregate_extractions, make_extract_node, route_documents
from agents.intake import build_intake_graph
from agents.orchestrator import (
    FINALIZE,
    HUMAN_REVIEW,
    NODE_NAME,
    TASK_NODES,
    finalize_node,
    human_review_node,
    orchestrator_node,
    route,
)
from agents.review import make_mcp_tool_caller, make_review_node
from state import SilverFileState

REPO_ROOT = Path(__file__).resolve().parent.parent


class _VisionExtractorOnFirstUse:
    """Creates the vision model only when a W-2 is actually read, so building the graph (for
    example to review a paused session) needs no API key."""

    def __init__(self) -> None:
        self._extractor = None

    @property
    def name(self) -> str:
        return self._extractor.name if self._extractor else "vision-llm"

    def read(self, image):
        if self._extractor is None:
            from dotenv import load_dotenv

            load_dotenv()
            self._extractor = LLMVisionW2Extractor()
        return self._extractor.read(image)


def _on_main_state(fn: Callable[[Any], Any]) -> Callable[[SilverFileState], Any]:
    """Run an agent's node or routing function on the main graph's state.

    LangGraph reads the state type a function declares and adds that type's reducers to the
    graph. Extraction and Eligibility declare their own state types, whose ``escalations``
    reducer differs from state.py's, and LangGraph refuses two reducers for one key. The agents
    keep their types for running on their own; in this graph they run on SilverFileState.
    """

    def run(state: SilverFileState) -> Any:
        return fn(state)

    run.__name__ = fn.__name__
    return run


def _start_extraction(state: dict[str, Any]) -> dict[str, Any]:
    """Entry to the extraction branch. Does nothing itself: Extraction's own conditional edge
    fans out from here, so it never clashes with the orchestrator's."""
    return {}


def build_graph(checkpointer=None, *, extractor=None, computation_caller=None, review_caller=None):
    """Compile the SilverFile graph.

    ``checkpointer``: from ``memory.make_checkpointer``; needed for interrupts and resuming.
    ``extractor``, ``computation_caller``, ``review_caller``: test doubles. By default W-2s are
    read by the vision model and both agents call the tax_math MCP server.
    """
    builder = StateGraph(SilverFileState)
    builder.add_node(NODE_NAME, orchestrator_node)

    builder.add_node("intake", build_intake_graph())  # subgraph; uses this graph's checkpointer
    # Extraction, wired as agents.extraction.add_extraction_nodes does: one branch per W-2,
    # then one aggregation step.
    builder.add_node("extraction", _start_extraction)
    builder.add_node("extract_document", make_extract_node(extractor or _VisionExtractorOnFirstUse()))
    builder.add_node("aggregate_extractions", _on_main_state(aggregate_extractions))
    builder.add_conditional_edges("extraction", _on_main_state(route_documents),
                                  ["extract_document", "aggregate_extractions"])
    builder.add_edge("extract_document", "aggregate_extractions")
    builder.add_edge("aggregate_extractions", NODE_NAME)

    builder.add_node(ELIGIBILITY_NODE, _on_main_state(eligibility_node))
    builder.add_edge(ELIGIBILITY_NODE, NODE_NAME)
    builder.add_node("computation", make_computation_node(computation_caller))
    builder.add_node("review", make_review_node(review_caller or make_mcp_tool_caller(REPO_ROOT)))
    builder.add_node(HUMAN_REVIEW, human_review_node)
    builder.add_node(FINALIZE, finalize_node)

    builder.add_edge(START, NODE_NAME)
    builder.add_conditional_edges(NODE_NAME, route, [*TASK_NODES.values(), HUMAN_REVIEW, FINALIZE])
    for node in ("intake", "computation", "review", HUMAN_REVIEW):
        builder.add_edge(node, NODE_NAME)
    builder.add_edge(FINALIZE, END)
    return builder.compile(checkpointer=checkpointer, name="silverfile")
