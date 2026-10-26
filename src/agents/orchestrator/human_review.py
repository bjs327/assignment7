"""Human review: the one place a person steps in. Part of the Orchestrator, not an agent of its
own, and reached only through the Orchestrator.

When any escalation is open, the Orchestrator sends the run here. This node:

1. lists every open escalation, from every agent, with the options a preparer has for it
   (``options_for`` below; the table is in docs/IMPLEMENTATION_NOTES.md);
2. pauses the run with LangGraph's ``interrupt()``, carrying a readable review packet and the
   same details as data, for the terminal program below or a dashboard;
3. checks the preparer's answer, and pauses again with the problems listed if anything is wrong;
4. records each decision and hands control back to the Orchestrator, which decides what runs
   next. This node never routes and never writes the task ledger.

The person here is a Kushim preparer, not the taxpayer. Only Intake talks to the taxpayer: a
"re_ask" decision hands Intake the questions to ask again and the preparer's note to show
before them.

The preparer's answer::

    {"decided_by": "preparer_kim",
     "decisions": [{"escalation_id": "eligibility_rules:spouse_died_in_tax_year:-:1",
                    "decision": "choose_filing_status",
                    "filing_status": "married_filing_jointly",
                    "note": "Spouse died in March; joint return allowed for 2025"}]}
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any

from langgraph.types import Command, interrupt

from agents.eligibility.corpus import citation_records
from agents.intake import question_plan, question_text
from memory import load_session, make_checkpointer, session_config
from state import HumanDecision
from tax_math.models import FilingStatus

from .agent import open_escalations

NODE_NAME = "human_review"

DECISIONS = {
    "choose_filing_status": "Choose the filing status yourself",
    "accept_documents": "Accept the documents as they are",
    "re_ask": "Re-ask the taxpayer",
    "re_read": "Re-read the document",
    "dismiss": "Dismiss: false alarm",
    "noted": "Noted, I'll follow up with the taxpayer",
    "checked_safe": "Checked: safe to continue",
    "stop": "Stop: SilverFile ends here and a preparer takes over",
}
NOTE_REQUIRED = {"checked_safe"}  # the preparer must say why
FILING_STATUSES = [s.value for s in FilingStatus]

AGENT_LABELS = {
    "intake": "Intake",
    "document_extraction": "Document Extraction",
    "eligibility_rules": "Eligibility & Rules",
    "computation": "Computation",
    "review": "Review",
    "orchestrator": "Orchestrator",
}
INTAKE_TITLES = {
    "tax_advice_request": "Tax advice requested",
    "financial_exploitation": "Possible financial exploitation or third-party pressure",
    "out_of_scope": "Something out of scope was mentioned",
    "conflicting_information": "The taxpayer's answers contradict each other",
}
ELIGIBILITY_TITLES = {
    "filing_status_ambiguity": "Filing status unclear",
    "user_document_conflict": "An answer doesn't match a document",
    "out_of_scope": "Out of scope",
    "taxable_amount_not_determined": "1099-R taxable amount not determined",
    "security": "SSN outside the test range",
}
ORCHESTRATOR_TITLES = {
    "handoff_cycle": "Work is going round in circles",
    "review_intake_request": "Review found a problem in the taxpayer's answers",
    "computation_error": "A calculation couldn't be completed",
    "computation_needs_human": "A calculation needs a person",
    "no_runnable_task": "Work is stuck",
}
_READ_AGAIN = {"unreadable_box", "missing_wages", "extraction_failed", "file_unreadable"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- What a preparer may decide ----------------------------------------------------------------


def options_for(agent: str, record: dict[str, Any]) -> list[str]:
    """The decisions allowed for one escalation. Anything unrecognised gets re-ask or stop only,
    so nothing carries on past a problem this table doesn't know."""
    if agent == "intake":
        kind = record.get("type")
        if kind == "tax_advice_request":
            return ["noted", "stop"]
        if kind == "financial_exploitation":
            return ["checked_safe", "stop"]
        if kind == "out_of_scope" and record.get("field") != "documents":
            return ["dismiss", "stop"]
        return ["re_ask", "stop"]  # contradictory answers, or an out-of-scope document listed
    if agent == "eligibility_rules":
        category = record.get("category")
        if category == "taxable_amount_not_determined":
            return ["stop"]
        if category == "filing_status_ambiguity":
            return ["choose_filing_status", "re_ask", "stop"]
        if category == "user_document_conflict":
            return ["accept_documents", "re_ask", "stop"]
        return ["re_ask", "stop"]  # out of scope, or an SSN outside the test range
    if agent == "document_extraction":
        code = record.get("code") or ""
        if code in _READ_AGAIN or code.startswith("amount_"):
            return ["re_read", "stop"]
        return ["re_ask", "stop"]  # wrong year, uploaded twice, SSN outside the test range
    return ["re_ask", "stop"]  # Review, the Orchestrator, or anything else


def _title(agent: str, record: dict[str, Any]) -> str:
    if agent == "intake":
        if record.get("type") == "out_of_scope" and record.get("field") == "documents":
            return "An out-of-scope document was listed"
        return INTAKE_TITLES.get(record.get("type"), "Intake raised a flag")
    if agent == "eligibility_rules":
        return ELIGIBILITY_TITLES.get(record.get("category"), "Eligibility & Rules needs a person")
    if agent == "document_extraction":
        return "A document needs checking"
    if agent == "review":
        return "Review needs a person"
    return ORCHESTRATOR_TITLES.get(record.get("code"), "The run needs a person")


def _issue(item: dict[str, Any]) -> dict[str, Any]:
    agent, record = item["source_agent"], item["record"]
    rule_ids = record.get("rule_ids") or []
    try:
        rules = [f"{c['id']} {c['title']}" for c in citation_records(rule_ids)]
    except KeyError:
        rules = list(rule_ids)
    return {
        "escalation_id": item["id"],
        "source_agent": agent,
        "raised_by": AGENT_LABELS.get(agent, agent),
        "title": _title(agent, record),
        "reason": record.get("reason") or "",
        "document_id": record.get("document_id"),
        "taxpayer_said": record.get("excerpt") if agent == "intake" else None,
        "rules": rules,
        "options": [{"decision": d, "label": DECISIONS[d]} for d in options_for(agent, record)],
        "record": record,
    }


def intake_questions(state: dict[str, Any]) -> list[str]:
    """The questions Intake can ask again, from Intake's own question plan."""
    return [f for f in question_plan(state.get("intake_answers") or {}) if f != "confirm"]


# --- The review packet -------------------------------------------------------------------------


def _packet_text(state: dict[str, Any], issues: list[dict[str, Any]]) -> str:
    ledger = state.get("task_ledger") or {}
    lines = [
        "SILVERFILE: PREPARER REVIEW NEEDED",
        f"Session: {state.get('session_id') or '(no session id)'}",
        "Progress: " + " · ".join(f"{task} {rec['status']}" for task, rec in ledger.items()),
        "",
    ]
    for n, issue in enumerate(issues, start=1):
        lines.append(f"ISSUE {n} of {len(issues)}: {issue['title']}  (raised by {issue['raised_by']})")
        lines.append(f"  What happened: {issue['reason']}")
        if issue["taxpayer_said"]:
            lines.append(f'  The taxpayer said: "{issue["taxpayer_said"]}"')
        if issue["document_id"]:
            lines.append(f"  Document: {issue['document_id']}")
        if issue["rules"]:
            lines.append("  Rules: " + "; ".join(issue["rules"]))
        lines.append("  Options:")
        lines += [f"    [{i}] {o['label']}" for i, o in enumerate(issue["options"], start=1)]
        lines.append("")
    return "\n".join(lines)


def _payload(state: dict[str, Any], issues: list[dict[str, Any]], questions: list[str],
             problems: list[str]) -> dict[str, Any]:
    answers = state.get("intake_answers") or {}
    return {
        "type": "human_review",  # Intake's pauses are "intake_question"
        "session_id": state.get("session_id"),
        "packet": _packet_text(state, issues),
        "problems": problems,  # why the last answer was refused, if it was
        "issues": [{k: v for k, v in i.items() if k != "record"} for i in issues],
        "intake_questions": [{"field": f, "question": question_text(f, answers)} for f in questions],
        "filing_statuses": [{"value": s.value, "label": s.label} for s in FilingStatus],
    }


# --- Checking and recording the answer ---------------------------------------------------------


def check_answer(answer: Any, issues: list[dict[str, Any]],
                 questions: list[str]) -> tuple[list[HumanDecision], list[str]]:
    """Turn the preparer's answer into decisions. Returns (decisions, problems); any problem
    means nothing is recorded."""
    if not isinstance(answer, dict):
        return [], ["The answer must give a name and one decision per issue."]
    problems: list[str] = []
    who = str(answer.get("decided_by") or "").strip()
    if not who:
        problems.append("Please give your name, so each decision is recorded against it.")

    given: dict[Any, dict] = {}
    for d in answer.get("decisions") or []:
        eid = d.get("escalation_id") if isinstance(d, dict) else None
        if eid in given:
            problems.append(f"{eid} has more than one decision.")
        given[eid] = d if isinstance(d, dict) else {}
    known = {i["escalation_id"] for i in issues}
    problems += [f"{eid} isn't an open issue." for eid in given if eid not in known]

    decisions: list[HumanDecision] = []
    for n, issue in enumerate(issues, start=1):
        d = given.get(issue["escalation_id"])
        if d is None:
            problems.append(f"Issue {n} ({issue['title']}) has no decision.")
            continue
        choice = d.get("decision")
        allowed = [o["decision"] for o in issue["options"]]
        if choice not in allowed:
            problems.append(f"Issue {n}: choose one of: " + "; ".join(DECISIONS[a] for a in allowed) + ".")
            continue
        note = str(d.get("note") or "").strip()
        decision = HumanDecision(escalation_id=issue["escalation_id"], decision=choice, note=note, decided_by=who)
        if choice == "choose_filing_status":
            if d.get("filing_status") not in FILING_STATUSES:
                problems.append(f"Issue {n}: choose one of the five filing statuses.")
            decision["filing_status"] = d.get("filing_status")
        if choice == "re_ask":
            fields = d.get("reask_fields")
            fields = list(fields) if isinstance(fields, (list, tuple)) else []
            unknown = [f for f in fields if f not in questions]
            if not fields or unknown:
                problems.append(f"Issue {n}: name at least one question Intake can ask"
                                + (f" (not on Intake's list: {', '.join(map(str, unknown))})" if unknown else "") + ".")
            decision["reask_fields"] = fields
        if choice in NOTE_REQUIRED and not note:
            problems.append(f'Issue {n}: "{DECISIONS[choice]}" needs a note saying why it is safe to continue.')
        decisions.append(decision)
    return decisions, problems


def record_decisions(issues: list[dict[str, Any]], decisions: list[HumanDecision]) -> dict[str, Any]:
    """The state update for a checked set of decisions."""
    now = _now()
    by_id = {i["escalation_id"]: i for i in issues}
    update: dict[str, Any] = {"human_decisions": [], "escalations": [], "human_resolutions": [],
                              "preparer_notes": []}
    reask: list[str] = []
    notices: list[str] = []
    reread: list[str] = []

    for d in decisions:
        d = HumanDecision(**d, decided_at=now)
        issue = by_id[d["escalation_id"]]
        record, choice = issue["record"], d["decision"]
        update["human_decisions"].append(d)
        if issue["source_agent"] != "intake":  # Intake's flags stay in Intake's list; the decision closes them
            update["escalations"].append({**record, "resolved": True, "resolution": {
                "decision": choice, "note": d["note"], "decided_by": d["decided_by"], "decided_at": now}})
        if choice in ("choose_filing_status", "accept_documents"):
            update["human_resolutions"].append({  # facts.HumanResolution, which Eligibility reads
                "code": record.get("code"), "document_id": record.get("document_id"), "decision": "approved",
                "filing_status": d.get("filing_status"), "resolved_by": d["decided_by"],
                "resolved_at": now, "note": d["note"]})
        elif choice == "re_ask":
            reask += [f for f in d["reask_fields"] if f not in reask]
            if d["note"]:
                notices.append(d["note"])
        elif choice == "re_read" and record.get("document_id"):
            reread.append(record["document_id"])
        elif choice in ("noted", "checked_safe", "dismiss"):
            update["preparer_notes"].append({
                "escalation_id": d["escalation_id"], "decision": choice, "issue": issue["title"],
                "reason": issue["reason"], "taxpayer_said": issue["taxpayer_said"], "note": d["note"],
                "decided_by": d["decided_by"], "decided_at": now})

    if reask:
        update["intake_reask"] = reask
        update["intake_notice"] = " ".join(notices)
    if reread:
        update["reextract_document_ids"] = sorted(set(reread))
    return {k: v for k, v in update.items() if v != []}


# --- The node ----------------------------------------------------------------------------------


def human_review_node(state: dict[str, Any]) -> dict[str, Any]:
    issues = [_issue(item) for item in open_escalations(state)]
    if not issues:
        return {}
    questions = intake_questions(state)
    problems: list[str] = []
    while True:  # pause again, with the problems listed, until the answer checks out
        answer = interrupt(_payload(state, issues, questions, problems))
        decisions, problems = check_answer(answer, issues, questions)
        if not problems:
            return record_decisions(issues, decisions)


# --- For the terminal program and, later, the dashboard ----------------------------------------


def pending_review(graph, session_id: str) -> dict[str, Any] | None:
    """The review packet a session is waiting on, or None."""
    for value in load_session(graph, session_id).pending:
        if isinstance(value, dict) and value.get("type") == "human_review":
            return value
    return None


def submit_decisions(graph, session_id: str, answer: dict[str, Any]) -> dict[str, Any] | None:
    """Send a preparer's answer to a waiting session. Returns the next review packet if the
    session pauses for review again (for example because the answer had problems), else None."""
    if pending_review(graph, session_id) is None:
        raise RuntimeError(f"session {session_id!r} is not waiting for a preparer")
    graph.invoke(Command(resume=answer), session_config(session_id))
    return pending_review(graph, session_id)


# --- Terminal program: python run.py review --session ID ---------------------------------------


def _ask(prompt: str, *, required: bool = True) -> str:
    while True:
        text = input(prompt).strip()
        if text or not required:
            return text
        print("This one is required.")


def _pick(prompt: str, labels: list[str]) -> int | None:
    for i, label in enumerate(labels, start=1):
        print(f"  [{i}] {label}")
    while True:
        raw = input(prompt).strip().lower()
        if raw == "q":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(labels):
            return int(raw) - 1
        print(f"Please enter a number from 1 to {len(labels)}, or q.")


def _pick_many(prompt: str, labels: list[str]) -> list[int] | None:
    for i, label in enumerate(labels, start=1):
        print(f"  [{i}] {label}")
    while True:
        raw = input(prompt).strip().lower()
        if raw == "q":
            return None
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if parts and all(p.isdigit() and 1 <= int(p) <= len(labels) for p in parts):
            return list(dict.fromkeys(int(p) - 1 for p in parts))
        print(f"Please enter numbers from 1 to {len(labels)}, separated by commas, or q.")


def _ask_decisions(packet: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Ask about each issue in turn. None if the preparer leaves the session paused."""
    out = []
    issues = packet["issues"]
    for n, issue in enumerate(issues, start=1):
        print(f"\nIssue {n} of {len(issues)}: {issue['title']}")
        pick = _pick("Choose an option, or q to leave the session paused: ",
                     [o["label"] for o in issue["options"]])
        if pick is None:
            return None
        choice = issue["options"][pick]["decision"]
        d: dict[str, Any] = {"escalation_id": issue["escalation_id"], "decision": choice}
        if choice == "choose_filing_status":
            statuses = packet["filing_statuses"]
            p = _pick("Filing status: ", [s["label"] for s in statuses])
            if p is None:
                return None
            d["filing_status"] = statuses[p]["value"]
        if choice == "re_ask":
            questions = packet["intake_questions"]
            picks = _pick_many("Questions to ask again (numbers separated by commas): ",
                               [f"{q['field']}: {q['question'].splitlines()[0][:90]}" for q in questions])
            if picks is None:
                return None
            d["reask_fields"] = [questions[i]["field"] for i in picks]
        if choice in NOTE_REQUIRED:
            d["note"] = _ask("Why is it safe to continue? (required): ")
        elif choice == "re_ask":
            d["note"] = _ask("Note to show the taxpayer before the questions (optional): ", required=False)
        else:
            d["note"] = _ask("Note for the record (optional): ", required=False)
        out.append(d)
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Review a SilverFile session that is waiting for a preparer.")
    parser.add_argument("--session", required=True, help="session ID to review")
    parser.add_argument("--db", default=None, help="SQLite file for saved sessions (default: data/sessions.sqlite)")
    args = parser.parse_args(argv)

    from graph import build_graph
    from memory import DEFAULT_DB_PATH

    graph = build_graph(checkpointer=make_checkpointer(args.db or DEFAULT_DB_PATH))
    packet = pending_review(graph, args.session)
    if packet is None:
        print(f"Session {args.session} isn't waiting for a preparer.")
        return
    name = ""
    while packet:
        if packet["problems"]:
            print("\nNothing was recorded. Please fix:\n" + "\n".join(f"  - {p}" for p in packet["problems"]))
        print("\n" + packet["packet"])
        name = name or _ask("Your name (recorded with every decision): ")
        decisions = _ask_decisions(packet)
        if decisions is None:
            print("\nNothing was recorded. The session stays paused until someone reviews it.")
            return
        packet = submit_decisions(graph, args.session, {"decided_by": name, "decisions": decisions})
    status = load_session(graph, args.session).values.get("run_status")
    print(f"\nDecisions recorded. Session {args.session} is now: {status}.")


if __name__ == "__main__":
    main()
