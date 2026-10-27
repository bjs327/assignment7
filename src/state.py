"""Shared LangGraph state: the schema every agent reads and writes, and the reducers that
merge concurrent writes (for example from the parallel document-extraction branch).

Owner: Verena. Each agent keeps its own state type (IntakeState, ExtractionState,
EligibilityState, ComputationState, ReviewState) for running on its own. SilverFileState is
the union the main graph runs on, and where a key has a reducer here, that is the one that
applies in the main graph.

Who writes what
---------------
- Intake: session_id, intake_* keys (including intake_requests, its record of every field
  another agent asked for, and intake_message, what the taxpayer was told when the interview
  stopped for a person), needs_human_review
- Document Extraction: extracted_documents, extraction_complete, escalations, trace
- Eligibility & Rules: eligibility, eligibility_attempt, eligibility_reask, eligibility_notice,
  escalations, trace
- Computation: draft_return, computation_status, computation_missing, computation_revision, trace
- Review: review_report, review_iteration, review_fingerprints, review_history,
  revision_requests, reextract_document_ids, escalations
- Orchestrator: next_step, route_history, task_ledger, run_status, final_package, and the
  instructions it hands Intake (intake_reask, intake_notice)
- Human review: human_decisions, human_resolutions, preparer_notes, and resolved escalations
- Read by agents but not written by any agent yet: documents, entered_documents,
  entered_amounts, schedules_entered, computed_lines
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from agents.extraction.agent import merge_extractions

# --- Escalations -----------------------------------------------------------------------------


def _escalation_key(e: dict) -> tuple[str, str, str, int]:
    return (
        str(e.get("source_agent") or ""),
        str(e.get("document_id") or ""),
        str(e.get("code") or ""),
        int(e.get("attempt") or 0),
    )


def escalation_id(e: dict) -> str:
    """Stable id for one escalation in ``escalations``, shown to the preparer and dashboard."""
    agent, doc, code, attempt = _escalation_key(e)
    return f"{agent}:{code}:{doc or '-'}:{attempt}"


def intake_escalation_id(e: dict) -> str:
    """Stable id for one of Intake's flags. Intake never repeats a (type, reason) pair."""
    return f"intake:{e.get('type')}:{e.get('reason')}"


def merge_escalation_records(left: list[dict] | None, right: list[dict] | None) -> list[dict]:
    """Merge escalations from every agent and from human review.

    * Keyed like Extraction's ``merge_escalations``: (agent, document, code, attempt).
    * A later write of the same key replaces the earlier one. Human review resolves an
      escalation by writing it back with ``resolved`` and ``resolution``. If the agent raises
      it again later because the problem is still there, the new record has neither, so the
      escalation is open again.
    * Sorted by key, so the result doesn't depend on which parallel branch finished first.
    """
    merged: dict[tuple, dict] = {}
    for item in (left or []) + (right or []):
        merged[_escalation_key(item)] = item
    return [merged[k] for k in sorted(merged)]


# --- Task ledger -----------------------------------------------------------------------------

TaskStatus = Literal["pending", "running", "done", "failed", "stale", "escalated", "stopped"]


class TaskRecord(TypedDict):
    task_id: str  # one task per agent stage, e.g. "eligibility"
    agent: str  # the graph node that does the work
    status: TaskStatus
    depends_on: list[str]  # task ids whose output this task reads
    attempts: int  # times the orchestrator has sent work to it
    created_at: str  # ISO 8601 UTC
    updated_at: str
    note: str  # why it has this status, in plain English


def merge_ledger(
    left: dict[str, TaskRecord] | None, right: dict[str, TaskRecord] | None
) -> dict[str, TaskRecord]:
    """Task records by id. A later record for a task replaces the earlier one."""
    return {**(left or {}), **(right or {})}


# --- Human review ----------------------------------------------------------------------------


class HumanDecision(TypedDict, total=False):
    escalation_id: str
    decision: str  # one of human_review.DECISIONS, e.g. "choose_filing_status" or "stop"
    filing_status: str | None  # set with "choose_filing_status"
    reask_fields: list[str]  # set with "re_ask": Intake fields to ask again
    note: str  # the preparer's note; with "re_ask" it is shown to the taxpayer
    decided_by: str
    decided_at: str  # ISO 8601 UTC


RunStatus = Literal["in_progress", "awaiting_human", "approved", "handed_to_preparer"]


# --- The main graph's state ------------------------------------------------------------------


class SilverFileState(TypedDict, total=False):
    session_id: str

    # Intake
    # "awaiting_human": the interview stopped for a flag a person must decide on first.
    intake_status: Literal["in_progress", "complete", "awaiting_human"]
    intake_record: dict[str, Any] | None
    intake_answers: dict[str, Any]
    intake_corrections: list[dict[str, Any]]
    intake_escalations: list[dict[str, Any]]
    intake_reask: list[str]
    intake_notice: str
    # Declared here so they survive the Intake subgraph's return: LangGraph drops output keys
    # the parent state doesn't declare.
    intake_requests: dict[str, dict[str, Any]]  # field -> {requested, status, cleared}
    intake_message: str
    needs_human_review: bool

    # Documents
    documents: list[dict[str, Any]]
    reextract_document_ids: list[str]
    extracted_documents: Annotated[list[dict[str, Any]], merge_extractions]
    extraction_complete: bool
    entered_documents: list[dict[str, Any]]
    entered_amounts: dict[str, dict[str, Any]]
    schedules_entered: list[str]

    # Eligibility & Rules
    eligibility: dict[str, Any]
    eligibility_attempt: int
    eligibility_reask: list[str]
    eligibility_notice: str
    eligibility_facts: dict[str, Any]  # test and demo override only
    computed_lines: dict[str, int]

    # Computation
    draft_return: dict[str, Any]
    computation_status: str
    computation_missing: list[dict[str, str]]
    computation_revision: int

    # Review
    review_report: dict[str, Any]
    review_iteration: int
    review_fingerprints: Annotated[list[str], operator.add]
    review_history: Annotated[list[dict[str, Any]], operator.add]
    revision_requests: dict[str, list[dict[str, Any]]]

    # Shared by several agents
    escalations: Annotated[list[dict[str, Any]], merge_escalation_records]
    trace: Annotated[list[dict[str, Any]], operator.add]

    # Human review
    human_decisions: Annotated[list[HumanDecision], operator.add]  # audit record of every decision
    human_resolutions: Annotated[list[dict[str, Any]], operator.add]  # Eligibility's format
    preparer_notes: Annotated[list[dict[str, Any]], operator.add]

    # Orchestrator
    next_step: str
    route_history: Annotated[list[str], operator.add]  # node names, read by the loop guard
    task_ledger: Annotated[dict[str, TaskRecord], merge_ledger]
    run_status: RunStatus
    final_package: dict[str, Any]
