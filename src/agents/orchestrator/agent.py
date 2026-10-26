"""Orchestrator: decides which agent acts next and owns termination. It may not extract,
compute or talk to the taxpayer.

Hub and spoke: every agent, and the shared human review step, hands control back here, and
only this node chooses what runs next.

    intake, extraction, eligibility_rules, computation, review, human_review
                                  |  ^
                                  v  |
                              orchestrator ──> finalize ──> END

Each time it runs, the orchestrator:

1. records the outcome of the step that just ran in the task ledger, and marks finished work
   that read an earlier version of a re-run task's output as stale;
2. ends the run if a preparer chose "stop";
3. sends the run to human review if any escalation is open;
4. ends the run if Review approved the return and nothing is left to redo;
5. runs the loop guard, and sends the run to human review if the same hand-off keeps
   repeating;
6. otherwise sends work to the first task, in pipeline order, that is pending or stale and
   whose dependencies are done.

It reads status fields only. It never reads a dollar amount, never changes another agent's
output and never writes text for the taxpayer: the notices it hands Intake were written by
Eligibility & Rules, Computation or the preparer. Review send-backs to Intake describe a
problem without naming Intake's questions, so they go to the preparer, who picks them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agents.review.loopguard import detect_handoff_cycle
from agents.review.node import next_hops
from state import TaskRecord, escalation_id, intake_escalation_id

NODE_NAME = "orchestrator"
HUMAN_REVIEW = "human_review"
FINALIZE = "finalize"

# Tasks in pipeline order, the graph node that does each, and whose output each one reads.
TASK_NODES = {
    "intake": "intake",
    "extraction": "extraction",
    "eligibility": "eligibility_rules",
    "computation": "computation",
    "review": "review",
}
DEPENDS_ON: dict[str, list[str]] = {
    "intake": [],
    "extraction": ["intake"],
    "eligibility": ["intake", "extraction"],
    "computation": ["eligibility", "extraction"],
    "review": ["computation"],
}
# The task an escalation belongs to, by the agent name it carries.
SOURCE_TASKS = {
    "intake": "intake",
    "document_extraction": "extraction",
    "eligibility_rules": "eligibility",
    "computation": "computation",
    "review": "review",
}
LOOP_REPEATS = 3  # a hand-off pattern repeated this many times in a row is a cycle


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- Task ledger -----------------------------------------------------------------------------


def _record(old: TaskRecord | None, task_id: str, status: str, note: str, *, routed: bool = False) -> TaskRecord:
    now = _now()
    return TaskRecord(
        task_id=task_id,
        agent=TASK_NODES[task_id],
        status=status,
        depends_on=DEPENDS_ON[task_id],
        attempts=(old["attempts"] if old else 0) + (1 if routed else 0),
        created_at=old["created_at"] if old else now,
        updated_at=now,
        note=note,
    )


def new_ledger() -> dict[str, TaskRecord]:
    return {t: _record(None, t, "pending", "Not started.") for t in TASK_NODES}


def downstream(task_id: str) -> list[str]:
    """Every task that reads this task's output, directly or not, in pipeline order."""
    found = {task_id}
    for t in TASK_NODES:  # pipeline order, so a task's dependencies are already in found
        if any(d in found for d in DEPENDS_ON[t]):
            found.add(t)
    return [t for t in TASK_NODES if t in found and t != task_id]


# --- Escalations -----------------------------------------------------------------------------


def open_escalations(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Every escalation no person has decided on yet, from every agent, as one list."""
    out = []
    for e in state.get("escalations") or []:
        if not e.get("resolved"):
            out.append({"id": escalation_id(e), "source_agent": e.get("source_agent"), "record": e})
    decided = {d.get("escalation_id") for d in state.get("human_decisions") or []}
    for e in state.get("intake_escalations") or []:
        if intake_escalation_id(e) not in decided:
            out.append({"id": intake_escalation_id(e), "source_agent": "intake", "record": e})
    return out


def _escalation(code: str, reason: str, attempt: int) -> dict[str, Any]:
    """An escalation the orchestrator raises itself, in the shape the other agents use."""
    return {"source_agent": NODE_NAME, "document_id": None, "code": code, "reason": reason,
            "attempt": attempt, "created_at": _now()}


# --- One visit -------------------------------------------------------------------------------


class _Visit:
    """Collects ledger changes, other state updates and new escalations for one visit."""

    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.ledger = dict(state.get("task_ledger") or new_ledger())
        self.history = list(state.get("route_history") or [])
        self.update: dict[str, Any] = {}
        self.raised: list[dict[str, Any]] = []

    def set(self, task_id: str, status: str, note: str, *, routed: bool = False) -> None:
        self.ledger[task_id] = _record(self.ledger.get(task_id), task_id, status, note, routed=routed)

    def reopen(self, task_id: str, note: str) -> None:
        """Send a task back to pending, and mark finished work that read its output as stale."""
        self.set(task_id, "pending", note)
        for t in downstream(task_id):
            if self.ledger[t]["status"] == "done":
                self.set(t, "stale", f"Reads the output of {task_id}, which has to run again.")

    def ask_intake(self, fields: list[str], notice: str) -> None:
        fields = list(dict.fromkeys((self.update.get("intake_reask") or []) + fields))
        self.update["intake_reask"] = fields
        self.update["intake_notice"] = " ".join(x for x in (self.update.get("intake_notice"), notice) if x)
        self.reopen("intake", "Asked to check: " + ", ".join(fields) + ".")

    def escalate(self, code: str, reason: str) -> None:
        self.raised.append(_escalation(code, reason, len(self.history)))

    def go(self, step: str, run_status: str) -> dict[str, Any]:
        update = {**self.update, "next_step": step, "route_history": [step],
                  "task_ledger": self.ledger, "run_status": run_status}
        if self.raised:
            update["escalations"] = self.raised
        return update


# --- Step 1: record what the last step produced ----------------------------------------------


def _after_intake(v: _Visit) -> None:
    if v.state.get("intake_status") == "complete":
        v.set("intake", "done", "Interview complete.")
        v.update.update(intake_reask=[], intake_notice="")  # Intake doesn't clear its inputs
        for t in downstream("intake"):
            if v.ledger[t]["status"] == "done":
                v.set(t, "stale", "Intake ran again, so its answers may have changed.")
    else:
        v.set("intake", "pending", "Interview paused before the end.")


def _after_extraction(v: _Visit) -> None:
    v.set("extraction", "done", "Documents read.")


def _after_eligibility(v: _Visit) -> None:
    status = (v.state.get("eligibility") or {}).get("status")
    if status == "complete":
        v.set("eligibility", "done", "Eligibility decided.")
    elif status == "needs_facts":
        v.set("eligibility", "pending", "Waiting for answers from the taxpayer.")
        v.ask_intake(v.state.get("eligibility_reask") or [], v.state.get("eligibility_notice") or "")
    else:
        v.set("eligibility", "escalated", "Waiting for a preparer.")


def _after_computation(v: _Visit) -> None:
    status = v.state.get("computation_status")
    missing = v.state.get("computation_missing") or []
    if status == "complete":
        v.set("computation", "done", "Draft return computed.")
    elif status == "blocked":
        v.set("computation", "pending", "Waiting for Eligibility & Rules.")
        v.reopen("eligibility", "Computation needs its decision.")
    elif status == "needs_input":
        v.set("computation", "pending", "Missing: " + ", ".join(m["field"] for m in missing) + ".")
        to_intake = [m for m in missing if m.get("route_to") == "intake"]
        if to_intake:
            v.ask_intake([m["field"] for m in to_intake], " ".join(m["reason"] for m in to_intake))
        documents = sorted({m["field"].split(".")[1] for m in missing
                            if m.get("route_to") == "extraction" and m["field"].startswith("document.")})
        if documents:
            v.update["reextract_document_ids"] = documents
            v.reopen("extraction", "Computation needs documents read again.")
        if any(m.get("route_to") == "eligibility" for m in missing):
            v.reopen("eligibility", "Computation needs a document cleared.")
        to_human = [m for m in missing if m.get("route_to") not in ("intake", "extraction", "eligibility")]
        if to_human:
            v.escalate("computation_needs_human", " ".join(m["reason"] for m in to_human))
    else:  # "error": a tax_math tool refused inputs that had passed every check
        v.set("computation", "escalated", "A calculation tool refused its inputs.")
        v.escalate("computation_error", " ".join(m["reason"] for m in missing) or "Computation failed.")


def _after_review(v: _Visit) -> None:
    report = v.state.get("review_report") or {}
    hops = next_hops(report)
    if hops == ["done"]:
        v.set("review", "done", "Approved.")
    elif hops == ["end_for_human"]:
        v.set("review", "escalated", report.get("summary") or "Review needs a preparer.")
    else:
        v.set("review", "pending", f"Waiting for revisions (review {report.get('iteration')}).")
        for task in hops:
            if task == "intake":
                requests = (v.state.get("revision_requests") or {}).get("intake") or []
                v.escalate("review_intake_request", "Review found problems in the taxpayer's answers: "
                           + " ".join(r.get("message", "") for r in requests))
            else:
                v.reopen(task, f"Review sent work back (review {report.get('iteration')}).")


def _after_human_review(v: _Visit) -> None:
    """Put escalated tasks back to work once a preparer has decided on all their issues."""
    still_open = {SOURCE_TASKS.get(e["source_agent"]) for e in open_escalations(v.state)}
    for task, rec in list(v.ledger.items()):
        if rec["status"] == "escalated" and task not in still_open:
            if task == "intake" and v.state.get("intake_status") == "complete":
                v.set("intake", "done", "A preparer decided on the interview's flags.")
            else:
                v.reopen(task, "A preparer made a decision; run again.")
    if v.state.get("intake_reask"):
        v.reopen("intake", "A preparer asked for answers to be checked: "
                 + ", ".join(v.state["intake_reask"]) + ".")
    if v.state.get("reextract_document_ids"):
        v.reopen("extraction", "A preparer asked for documents to be read again.")


AFTER = {
    "intake": _after_intake,
    "extraction": _after_extraction,
    "eligibility_rules": _after_eligibility,
    "computation": _after_computation,
    "review": _after_review,
    HUMAN_REVIEW: _after_human_review,
}


# --- Steps 2 to 6: choose what runs next -----------------------------------------------------


def orchestrator_node(state: dict[str, Any]) -> dict[str, Any]:
    v = _Visit(state)
    if v.history and v.history[-1] in AFTER:
        AFTER[v.history[-1]](v)

    # 2. A preparer chose "stop": the run ends with a handoff package.
    if any(d.get("decision") == "stop" for d in state.get("human_decisions") or []):
        for task, rec in v.ledger.items():
            if rec["status"] != "done":
                v.set(task, "stopped", "A preparer stopped the run.")
        return v.go(FINALIZE, "handed_to_preparer")

    # 3. Anything open goes to a person before any more work is done.
    open_now = open_escalations(state) + [{"source_agent": NODE_NAME}] * len(v.raised)
    if open_now:
        for e in open_now:
            task = SOURCE_TASKS.get(e["source_agent"])
            if task and v.ledger[task]["status"] != "escalated":
                v.set(task, "escalated", "Waiting for a preparer.")
        return v.go(HUMAN_REVIEW, "awaiting_human")

    # 4. Review approved and nothing is left to redo. Checked before the loop guard, so the
    #    revise loop that ends in an approval isn't mistaken for a cycle.
    if all(rec["status"] == "done" for rec in v.ledger.values()):
        return v.go(FINALIZE, "approved")

    # 5. Loop guard, over the hand-offs since a person last stepped in.
    since = v.history
    if HUMAN_REVIEW in since:
        since = since[len(since) - since[::-1].index(HUMAN_REVIEW):]
    cycle = detect_handoff_cycle(since, LOOP_REPEATS)
    if cycle:
        v.escalate("handoff_cycle", f"The hand-off {' -> '.join(cycle)} repeated {LOOP_REPEATS} "
                   "times in a row without finishing, so the run was paused.")
        return v.go(HUMAN_REVIEW, "awaiting_human")

    # 6. The first task that can run.
    for task, rec in v.ledger.items():
        if rec["status"] in ("pending", "stale") and all(v.ledger[d]["status"] == "done" for d in DEPENDS_ON[task]):
            v.set(task, "running", f"Sent by the orchestrator (run {rec['attempts'] + 1}).", routed=True)
            return v.go(TASK_NODES[task], "in_progress")

    v.escalate("no_runnable_task", "No task can run, and the return isn't approved.")
    return v.go(HUMAN_REVIEW, "awaiting_human")


def route(state: dict[str, Any]) -> str:
    """Conditional edge out of the orchestrator: the node it chose."""
    return state["next_step"]


# --- Termination -----------------------------------------------------------------------------


def finalize_node(state: dict[str, Any]) -> dict[str, Any]:
    """Gather the run's results into one package: the draft plus its audit record. It copies;
    it computes nothing."""
    decisions = state.get("human_decisions") or []
    stops = [d for d in decisions if d.get("decision") == "stop"]
    run_status = "handed_to_preparer" if stops else "approved"
    package = {
        "session_id": state.get("session_id"),
        "run_status": run_status,
        "finished_at": _now(),
        "stop_reasons": [{k: d.get(k) for k in ("escalation_id", "note", "decided_by", "decided_at")}
                         for d in stops],
        "draft_return": state.get("draft_return"),
        "intake_record": state.get("intake_record"),
        "extracted_documents": state.get("extracted_documents") or [],
        "entered_documents": state.get("entered_documents") or [],
        "eligibility": state.get("eligibility"),
        "review_report": state.get("review_report"),
        "escalations": state.get("escalations") or [],
        "intake_escalations": state.get("intake_escalations") or [],
        "human_decisions": decisions,
        "preparer_notes": state.get("preparer_notes") or [],
        "task_ledger": state.get("task_ledger") or {},
        "route_history": state.get("route_history") or [],
        "trace": state.get("trace") or [],
    }
    return {"final_package": package, "run_status": run_status}
