"""Eligibility & Rules as a LangGraph node.

        orchestrator --> eligibility_rules --> orchestrator

The node gathers facts from shared state (``facts.py``), runs the deterministic decision
table (``rules_engine.py``) and writes the decision back. It never talks to the taxpayer,
computes an amount, or calls ``interrupt()``. Instead it hands the Orchestrator:

* ``eligibility``: the full decision (status, line 12d boxes, who qualifies for the senior
  deduction, document routing, schedules, and every finding with its citations);
* ``eligibility_reask`` and ``eligibility_notice``: questions for Intake (map them onto
  Intake's ``intake_reask`` and ``intake_notice``);
* ``escalations``: one record per open escalation, in the same shape Document Extraction
  uses, for the Orchestrator to interrupt on and the dashboard to list.

Routing for the Orchestrator, by ``eligibility["status"]``:

| status        | next hop                                              |
|---------------|-------------------------------------------------------|
| needs_facts   | Intake, with ``eligibility_reask``                    |
| escalated     | human (interrupt), then back here with ``human_resolutions`` |
| complete      | Computation, with ``eligibility["computation_inputs"]``   |
"""

from __future__ import annotations

import operator
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from agents.extraction.agent import merge_escalations

from .facts import facts_from_state
from .rules_engine import EligibilityDecision, evaluate

AGENT_NAME = "eligibility_rules"
NODE_NAME = "eligibility_rules"


class EligibilityState(TypedDict, total=False):
    # Inputs (written by other agents)
    intake_record: dict | None
    intake_answers: dict[str, Any]
    extracted_documents: list[dict]
    entered_documents: list[dict]  # non-W-2 forms entered by hand: see facts.DocumentFacts
    schedules_entered: list[str]
    computed_lines: dict[str, int]
    human_resolutions: list[dict]  # see facts.HumanResolution
    eligibility_facts: dict[str, Any]  # direct override, for tests and demos
    # Outputs
    eligibility: dict
    eligibility_attempt: int
    eligibility_reask: list[str]
    eligibility_notice: str
    escalations: Annotated[list[dict], merge_escalations]
    trace: Annotated[list[dict], operator.add]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _notice(decision: EligibilityDecision) -> str:
    n = len(decision.missing_facts)
    if not n:
        return ""
    return (
        "Thank you. I need " + ("one more answer" if n == 1 else f"{n} more answers")
        + " to check which parts of the form apply to you. I'll ask one question at a time."
    )


def eligibility_node(state: EligibilityState) -> dict[str, Any]:
    attempt = state.get("eligibility_attempt", 0) + 1
    decision = evaluate(facts_from_state(state))
    created = _now()
    escalations = [
        {
            "source_agent": AGENT_NAME,
            # merge_escalations dedupes on (agent, document_id, code, attempt). A repeat of an
            # unresolved escalation on a later run is the same escalation, so attempt stays 1.
            "document_id": e.document_id,
            "code": e.code,
            "category": e.category,
            "reason": e.reason,
            "rule_ids": e.rule_ids,
            "resolvable": e.resolvable,
            "attempt": 1,
            "created_at": created,
        }
        for e in decision.escalations
    ]
    return {
        "eligibility": decision.model_dump(mode="json"),
        "eligibility_attempt": attempt,
        "eligibility_reask": [m.field for m in decision.missing_facts],
        "eligibility_notice": _notice(decision),
        "escalations": escalations,
        "trace": [{
            "agent": AGENT_NAME,
            "event": "evaluated",
            "at": created,
            "attempt": attempt,
            "status": decision.status,
            "missing_facts": len(decision.missing_facts),
            "escalations": [e.code for e in decision.escalations],
        }],
    }


def add_eligibility_node(builder: StateGraph, *, entry: str | None = None, exit_to: str | None = None) -> str:
    """Add the node to the main graph. Wire ``entry -> node -> exit_to`` if given (normally the
    Orchestrator both ways). Returns the node name for conditional edges."""
    builder.add_node(NODE_NAME, eligibility_node)
    if entry:
        builder.add_edge(entry, NODE_NAME)
    if exit_to:
        builder.add_edge(NODE_NAME, exit_to)
    return NODE_NAME


def build_eligibility_graph(checkpointer=None):
    """A standalone one-node graph, for tests and ``python run.py eligibility``."""
    builder = StateGraph(EligibilityState)
    add_eligibility_node(builder)
    builder.add_edge(START, NODE_NAME)
    builder.add_edge(NODE_NAME, END)
    return builder.compile(checkpointer=checkpointer)
