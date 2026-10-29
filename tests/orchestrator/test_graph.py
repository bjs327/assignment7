"""Tests of the whole SilverFile graph, with every real agent connected through the Orchestrator.

What is tested:
- A clean session runs start to finish: Intake, Document Extraction (both W-2s read in
  parallel), Eligibility & Rules, Computation and Review, ending in an approved draft.
- The review -> revise loop stops after exactly three reviews (specification section 4.1).
- The loop guard catches a real hand-off cycle, Intake and Eligibility & Rules passing the same
  work back and forth, and pauses for a person (section 4.1). Intake now asks every question
  Eligibility & Rules names, so the cycle is produced by an Eligibility & Rules that says it
  needs facts without naming any: the kind of agent fault the guard exists for.
- After a person steps in, the loop guard starts counting again from zero.
- A session paused for a preparer is saved, and picks up where it left off after a restart.

Uses the offline W-2 reader and the real tax_math tools, so no API key is needed."""

import pytest
from langgraph.types import Command

import graph as graph_module
from agents.eligibility import eligibility_node
from agents.orchestrator import pending_review, submit_decisions
from memory import load_session, make_checkpointer, session_config

from .scenarios import decide, lines, run, session


def test_clean_session_runs_to_an_approved_draft(graph):
    snap = run(graph, session())
    v = snap.values
    assert snap.pending == [] and v["run_status"] == "approved"
    assert v["route_history"] == ["intake", "extraction", "eligibility_rules", "computation", "review", "finalize"]
    assert {t: r["status"] for t, r in v["task_ledger"].items()} == dict.fromkeys(
        ["intake", "extraction", "eligibility", "computation", "review"], "done")
    assert lines(v)["1z"] == 24700 and lines(v)["25a"] == 2160  # both W-2s, read in parallel
    package = v["final_package"]
    assert package["run_status"] == "approved" and package["draft_return"]["status"] == "draft"
    assert package["intake_record"]["taxpayer"]["ssn"] == "900-12-3456"  # needed on the form


def _skewed(call):
    """Answers Review's re-checks a little differently every time, so each review finds new
    problems: the revise loop can only end at its bound."""
    calls = {"n": 0}

    def caller(tool, arguments):
        calls["n"] += 1
        result = dict(call(tool, arguments))
        result["value"] = result["value"] + calls["n"]
        return result

    return caller


def test_review_revise_loop_is_bounded_at_three_reviews(make_graph, call):
    graph = make_graph(review_caller=_skewed(call))
    snap = run(graph, session())
    v = snap.values
    assert v["review_iteration"] == 3
    assert [h["verdict"] for h in v["review_history"]] == ["revise", "revise", "escalate"]
    assert v["route_history"][-7:] == ["computation", "review", "computation", "review",
                                       "computation", "review", "human_review"]
    assert pending_review(graph, "session_1")["issues"][0]["escalation_id"].startswith(
        "review:review_iteration_limit")


def _needs_unnamed_facts(state):
    """Eligibility & Rules with a fault: it reports needs_facts but names no fact, so Intake
    has nothing to ask, finishes at once, and the same three hand-offs repeat."""
    result = eligibility_node(state)
    return {**result, "eligibility": {**result["eligibility"], "status": "needs_facts"},
            "eligibility_reask": [], "eligibility_notice": ""}


@pytest.fixture
def cycling_graph(make_graph, monkeypatch):
    monkeypatch.setattr(graph_module, "eligibility_node", _needs_unnamed_facts)
    return make_graph()


def test_loop_guard_breaks_the_intake_eligibility_cycle(cycling_graph):
    graph = cycling_graph
    snap = run(graph, session())
    route = snap.values["route_history"]
    assert route == ["intake", "extraction", "eligibility_rules"] * 3 + ["human_review"]
    packet = pending_review(graph, "session_1")
    assert [i["title"] for i in packet["issues"]] == ["Work is going round in circles"]
    assert [o["decision"] for o in packet["issues"][0]["options"]] == ["re_ask", "stop"]


def test_loop_guard_counts_again_after_a_person_steps_in(cycling_graph):
    graph = cycling_graph
    run(graph, session())
    packet = pending_review(graph, "session_1")
    submit_decisions(graph, "session_1", decide(packet, "re_ask", reask_fields=["filing_status"]))

    # The taxpayer is asked again, answers the same, and confirms the summary.
    question = load_session(graph, "session_1").pending[0]
    assert question["type"] == "intake_question" and question["field"] == "filing_status"
    graph.invoke(Command(resume="married filing jointly"), session_config("session_1"))
    assert load_session(graph, "session_1").pending[0]["field"] == "confirm"
    graph.invoke(Command(resume="yes"), session_config("session_1"))

    route = load_session(graph, "session_1").values["route_history"]
    after = route[route.index("human_review") + 1:]
    # The guard started counting afresh, and caught the same repeat again.
    assert after == ["intake", "extraction", "eligibility_rules"] * 3 + ["human_review"]


def test_a_saved_session_resumes_after_a_restart(make_graph, tmp_path):
    db = tmp_path / "sessions.sqlite"
    state = session()
    state["eligibility_facts"]["spouse_died_in_2025"] = True
    state["eligibility_facts"]["spouse_date_of_death"] = "2025-09-30"  # after turning 65 on 2025-07-18 (ELIG-02)
    run(make_graph(make_checkpointer(db)), state)

    # A new process: a new graph on the same SQLite file picks up the paused review.
    graph = make_graph(make_checkpointer(db))
    packet = pending_review(graph, "session_1")
    assert packet and packet["issues"][0]["title"] == "Filing status unclear"
    submit_decisions(graph, "session_1", decide(packet, "choose_filing_status",
                                                filing_status="married_filing_jointly"))
    assert load_session(graph, "session_1").values["run_status"] == "approved"
