"""Drive one SilverFile session through the full graph. Shared by ``python run.py session``
and the dashboard, so both behave the same.

A session moves through these states, read from its checkpoint (``memory.load_session``), never
from anything kept in memory, so a session can be resumed by another process days later:

- ``not_started``: nothing saved under this ID yet.
- ``question``: Intake is waiting for the taxpayer's answer to one question.
- ``preparer``: the run is paused for a Kushim preparer (human review).
- ``finished``: the run ended; ``final_package`` holds the draft and its audit record.

An answer containing a number that looks like a real SSN is refused before it reaches the
graph, so it is never written to the checkpoint.

Answers carry the ``question_id`` they were given for. An answer for a question that is no
longer the one waiting (a double click, a second browser tab) is ignored rather than applied to
the next question.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.types import Command

from memory import DEFAULT_DB_PATH, load_session, make_checkpointer, session_config


def open_graph(db_path: str | Path | None = None, *, offline: bool = False):
    """The full graph on a SQLite checkpointer (``data/sessions.sqlite`` by default).

    ``offline``: read W-2s with the offline test reader, which needs the ground-truth JSON next
    to each image (the demo W-2s have one). Otherwise the vision model reads them, which needs an
    API key in ``.env``.
    """
    from agents.computation import mcp_tool_caller
    from graph import build_graph

    # Review re-checks every line through tax_math. By default it starts a stdio MCP server per
    # call (about 35 seconds a run); the in-process MCP client still goes through the server and
    # its schema validation, as Computation's does, and keeps the dashboard responsive.
    options: dict[str, Any] = {"review_caller": mcp_tool_caller()}
    if offline:
        from agents.extraction import FixtureW2Extractor

        options["extractor"] = FixtureW2Extractor()
    return build_graph(make_checkpointer(db_path or DEFAULT_DB_PATH), **options)


def status(graph, session_id: str) -> dict[str, Any]:
    """Where the session is, and what the person looking at it needs to see."""
    snap = load_session(graph, session_id)
    values = snap.values
    if not snap.exists:
        return {"state": "not_started", "session_id": session_id, "values": {}}
    for item in snap.pending:
        if isinstance(item, dict) and item.get("type") == "intake_question":
            return {"state": "question", "session_id": session_id, "question": item, "values": values,
                    "intake_answers": _interview_answers(graph, session_id, values)}
        if isinstance(item, dict) and item.get("type") == "human_review":
            return {"state": "preparer", "session_id": session_id, "packet": item, "values": values,
                    "message": values.get("intake_message") or ""}
    if values.get("final_package"):
        return {"state": "finished", "session_id": session_id, "values": values,
                "run_status": values.get("run_status"), "package": values["final_package"]}
    if snap.next_nodes:  # stopped mid-step (for example a crash): carry on from the checkpoint
        graph.invoke(None, session_config(session_id))
        return status(graph, session_id)
    return {"state": "unknown", "session_id": session_id, "values": values}


def _interview_answers(graph, session_id: str, values: dict[str, Any]) -> dict[str, Any]:
    """The answers so far. Mid-interview they live in the Intake subgraph's checkpoint; the main
    state only receives them when Intake finishes."""
    snap = graph.get_state(session_config(session_id), subgraphs=True)
    for task in snap.tasks:
        if task.name == "intake" and getattr(task.state, "values", None) is not None:
            return dict(task.state.values.get("intake_answers") or {})
    return dict(values.get("intake_answers") or {})


def start(graph, session_id: str, *, documents: list[dict[str, Any]] | None = None,
          entered_documents: list[dict[str, Any]] | None = None,
          entered_amounts: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Start a new session with the documents registered so far. A session that already exists
    is not started again (that would be a duplicate submission); its status is returned."""
    current = status(graph, session_id)
    if current["state"] != "not_started":
        return current
    initial: dict[str, Any] = {"session_id": session_id, "documents": list(documents or []),
                               "entered_documents": list(entered_documents or [])}
    if entered_amounts:
        initial["entered_amounts"] = dict(entered_amounts)
    graph.invoke(initial, session_config(session_id))
    return status(graph, session_id)


def welcome_back(current: dict[str, Any]) -> str:
    """What a taxpayer sees on returning to a session that is waiting on a question: how far
    they got and a summary of what was already covered (specification section 4.3)."""
    from agents.intake import summarize_answers

    p = current["question"]["progress"]
    return (f"Welcome back! Your answers are saved: you've answered {p['answered']} of {p['total']} "
            "questions. Here is what we covered:\n"
            + summarize_answers(current.get("intake_answers") or {})
            + "\nLet's pick up where you left off.")


def answer(graph, session_id: str, question_id: str, text: str) -> dict[str, Any]:
    """Give the taxpayer's answer to the question with ``question_id``."""
    from agents.intake import ssn_refusal

    current = status(graph, session_id)
    if current["state"] != "question" or current["question"].get("question_id") != question_id:
        return {**current, "ignored": True}  # that question has already been answered
    if refusal := ssn_refusal(text):
        # Refused here, before the graph: LangGraph would save the answer in the checkpoint.
        return {**current, "question": {**current["question"], "notice": refusal}, "refused": True}
    graph.invoke(Command(resume=text), session_config(session_id))
    return status(graph, session_id)
