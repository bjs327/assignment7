"""The Review / Audit node.

Review reads the assembled draft return and tries to falsify it. It never
changes the return. It returns a verdict:

    approved  every blocker check passed; the return can go to a human reviewer
    revise    something is wrong that another agent can fix; work goes back
    escalate  a human must look (out of scope, SSN, open escalation, or the
              review loop is not converging)

The Orchestrator owns routing. It reads `review_report["verdict"]` and
`revision_requests` and decides the next hop (see next_hops()).
"""
from __future__ import annotations

import operator
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from .checks import ALL_CHECKS, ReviewContext
from .schemas import (
    APPROVED, BLOCKER, ESCALATE, HUMAN, MAX_REVIEW_ITERATIONS, REVISE, REVISION_ORDER,
    Finding, ReviewReport, fingerprint,
)
from .tools import ToolCaller


class ReviewState(TypedDict, total=False):
    """Keys Review reads and writes. Merge into src/state.py.

    Review reads: draft_return, documents, extracted_documents, escalations,
                  review_iteration, review_fingerprints
    Review writes: everything else below
    """

    draft_return: dict[str, Any]
    documents: list[dict[str, Any]]
    extracted_documents: list[dict[str, Any]]
    escalations: Annotated[list[dict[str, Any]], operator.add]

    review_report: dict[str, Any]  # latest report (overwritten each review)
    review_iteration: int  # how many reviews have run
    review_fingerprints: Annotated[list[str], operator.add]  # one per review that found blockers
    review_history: Annotated[list[dict[str, Any]], operator.add]  # compact log for the dashboard
    revision_requests: dict[str, list[dict[str, Any]]]  # agent -> what to fix
    reextract_document_ids: list[str]  # read by Document Extraction (already in its interface)


def _escalation(code: str, reason: str, iteration: int) -> dict[str, Any]:
    # Same shape Document Extraction uses, so the dashboard shows one list.
    return {
        "source_agent": "review",
        "document_id": None,
        "code": code,
        "reason": reason,
        "attempt": iteration,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def run_review(
    state: dict[str, Any],
    tool_caller: ToolCaller | None,
) -> tuple[ReviewReport, list[dict[str, Any]]]:
    """Pure core of the node: returns the report and any escalations to raise."""
    iteration = int(state.get("review_iteration", 0)) + 1
    ctx = ReviewContext(
        ret=state.get("draft_return") or {},
        documents=state.get("documents") or [],
        extracted=state.get("extracted_documents") or [],
        escalations=state.get("escalations") or [],
        tool_caller=tool_caller,
    )

    findings: list[Finding] = []
    checks_run: list[str] = []
    for check in ALL_CHECKS:
        checks_run.append(check.__name__)
        try:
            findings.extend(check(ctx))
        except Exception as exc:  # a crashing check must never look like a pass
            findings.append(Finding(
                f"CRASH-{check.__name__}", None, BLOCKER, HUMAN,
                f"Review check {check.__name__} crashed ({type(exc).__name__}), so the return is unverified.",
            ))

    blockers = [f for f in findings if f.severity == BLOCKER]
    fp = fingerprint(blockers) if blockers else ""
    escalations: list[dict[str, Any]] = []

    if not ctx.ret.get("lines"):
        # Nothing to review is not a pass.
        blockers.append(Finding("R00-empty", None, BLOCKER, HUMAN, "There is no draft return to review."))
        findings.append(blockers[-1])
        fp = fingerprint(blockers)

    if not blockers:
        verdict, summary = APPROVED, f"All {len(checks_run)} checks passed."
    elif any(f.route_to == HUMAN for f in blockers):
        verdict = ESCALATE
        summary = f"{sum(f.route_to == HUMAN for f in blockers)} finding(s) need a human."
        escalations.append(_escalation("review_needs_human", summary, iteration))
    elif fp in (state.get("review_fingerprints") or []):
        # Same blockers as an earlier review: the last revision changed nothing.
        verdict = ESCALATE
        summary = "The same problems came back after a revision, so the loop was stopped."
        escalations.append(_escalation("review_loop_detected", summary, iteration))
    elif iteration >= MAX_REVIEW_ITERATIONS:
        verdict = ESCALATE
        summary = f"Problems remain after {iteration} reviews, which is the limit."
        escalations.append(_escalation("review_iteration_limit", summary, iteration))
    else:
        verdict = REVISE
        summary = f"{len(blockers)} problem(s) sent back for revision."

    report = ReviewReport(
        iteration=iteration, verdict=verdict, findings=findings, checks_run=checks_run,
        fingerprint=fp, summary=summary,
    )
    if verdict == REVISE:
        for f in blockers:
            report.revision_requests.setdefault(f.route_to, []).append(
                {"finding_id": f.id, "line": f.line, "message": f.message, "expected": f.expected, "actual": f.actual}
            )
        report.reextract_document_ids = sorted({f.document_id for f in blockers if f.document_id})
    return report, escalations


def make_review_node(tool_caller: ToolCaller | None):
    """Build the graph node. Use: builder.add_node("review", make_review_node(caller))."""

    def review_node(state: dict[str, Any]) -> dict[str, Any]:
        report, escalations = run_review(state, tool_caller)
        update: dict[str, Any] = {
            "review_report": report.to_dict(),
            "review_iteration": report.iteration,
            "revision_requests": report.revision_requests,
            "review_history": [{
                "iteration": report.iteration, "verdict": report.verdict, "summary": report.summary,
                "blockers": sum(f.severity == BLOCKER for f in report.findings),
                "warnings": sum(f.severity != BLOCKER for f in report.findings),
                "at": datetime.now(timezone.utc).isoformat(),
            }],
        }
        if report.fingerprint:
            update["review_fingerprints"] = [report.fingerprint]
        if report.reextract_document_ids:
            update["reextract_document_ids"] = report.reextract_document_ids
        if escalations:
            update["escalations"] = escalations
        return update

    return review_node


def next_hops(report: dict[str, Any]) -> list[str]:
    """What the Orchestrator should do after a review, in dependency order.

    "end_for_human" means: stop and hand the draft plus audit record to a
    person (the Orchestrator raises the LangGraph interrupt for escalations).
    "done" means approved. Otherwise a list of agents to re-run, earliest first.
    """
    verdict = report.get("verdict")
    if verdict == APPROVED:
        return ["done"]
    if verdict == ESCALATE:
        return ["end_for_human"]
    agents = report.get("revision_requests") or {}
    return [a for a in REVISION_ORDER if a in agents]
