"""Intake's contract with the Orchestrator and the agents that ask it for information:
dynamic questions, what happens to each request, holds for a person, and interrupt safety.

Part one drives Intake on its own. Part two runs the full SilverFile graph (offline W-2 reader,
real tax_math tools), so the handoffs go through the real Orchestrator, Eligibility & Rules and
human review step.
"""

import pytest
from langgraph.types import Command

from agents.computation import mcp_tool_caller
from agents.eligibility.facts import _FACT_FIELDS, QUESTIONS, facts_from_state
from agents.extraction import FixtureW2Extractor
from agents.intake import (
    ADDITIONAL_QUESTIONS,
    MAX_REQUESTS_PER_FIELD,
    build_intake_graph,
    question_plan,
    request_corrections,
    start_or_resume,
    submit_answer,
)
from agents.orchestrator import pending_review, submit_decisions
from graph import build_graph
from memory import load_session, make_checkpointer, session_config
from state import intake_escalation_id
from tests.orchestrator.scenarios import decide, session

SINGLE_INTERVIEW = ["single", "912-34-5678", "03/14/1955", "no", "no", "a W-2", "no", "yes"]


@pytest.fixture
def graph():
    return build_intake_graph(make_checkpointer())


def finish(graph, sid="s"):
    turn = start_or_resume(graph, sid)
    for answer in SINGLE_INTERVIEW:
        turn = submit_answer(graph, sid, answer)
    assert turn["status"] == "complete"
    return turn


def values(graph, sid="s"):
    return load_session(graph, sid).values


def answer_all(graph, turn, answers, sid="s"):
    asked = []
    for answer in answers:
        assert turn["status"] == "awaiting_answer", turn
        asked.append(turn["field"])
        turn = submit_answer(graph, sid, answer)
    return turn, asked


# --- The field names other agents use -------------------------------------------------------


def test_every_question_eligibility_asks_is_one_intake_can_ask():
    standard = set(question_plan({"filing_status": "married_filing_separately"})) | {
        "spouse_ssn",
        "spouse_dob",
        "spouse_blind",
    }
    assert set(QUESTIONS) <= standard | set(ADDITIONAL_QUESTIONS)


def test_every_additional_answer_is_one_eligibility_reads():
    assert set(ADDITIONAL_QUESTIONS) <= _FACT_FIELDS


# --- Dynamic questions ----------------------------------------------------------------------


def test_requested_questions_are_asked_saved_and_reach_the_requesting_agent(graph):
    finish(graph)
    turn = request_corrections(
        graph,
        "s",
        ["spouse_died_in_2025", "can_be_claimed_as_dependent", "dual_status_alien"],
        "I need a few more answers.",
    )
    assert turn["notice"] == "I need a few more answers."
    turn, asked = answer_all(graph, turn, ["no", "no", "no"])
    assert asked == ["spouse_died_in_2025", "can_be_claimed_as_dependent", "dual_status_alien"]
    assert turn["field"] == "confirm"  # new answers are confirmed like any other
    assert "Someone else can claim you as a dependent: No" in turn["question"]
    out = submit_answer(graph, "s", "yes")["output"]

    assert out["intake_answers"]["spouse_died_in_2025"] is False
    assert out["intake_record"]["additional_facts"] == {
        "spouse_died_in_2025": False,
        "can_be_claimed_as_dependent": False,
        "dual_status_alien": False,
    }
    assert {f: r["status"] for f, r in out["intake_requests"].items()} == {
        "spouse_died_in_2025": "answered",
        "can_be_claimed_as_dependent": "answered",
        "dual_status_alien": "answered",
    }
    facts = facts_from_state(out)  # what Eligibility & Rules sees on its next run
    assert facts.spouse_died_in_2025 is False
    assert facts.can_be_claimed_as_dependent is False


def test_additional_answers_survive_later_answers_and_corrections(graph):
    finish(graph)
    turn = request_corrections(graph, "s", ["spouse_death_year"])
    assert turn["field"] == "spouse_death_year"
    turn = submit_answer(graph, "s", "It was 2023")
    turn = submit_answer(graph, "s", "change date of birth to 03/15/1955")
    assert turn["field"] == "confirm"
    out = submit_answer(graph, "s", "yes")["output"]
    assert out["intake_answers"]["spouse_death_year"] == 2023  # not pruned
    assert out["intake_record"]["taxpayer"]["date_of_birth"] == "1955-03-15"


def test_a_bad_answer_to_a_requested_question_asks_again(graph):
    finish(graph)
    request_corrections(graph, "s", ["spouse_death_year"])
    turn = submit_answer(graph, "s", "a while ago")
    assert turn["field"] == "spouse_death_year"
    assert "four digits" in turn["notice"]


def test_computations_name_for_line_6d_is_understood():
    graph = build_intake_graph(make_checkpointer())
    turn = start_or_resume(graph, "m")
    for answer in ["married filing separately", "yes", "912-34-5678", "03/14/1955", "no",
                   "913-11-2222", "01/02/1956", "no", "no", "a W-2", "no", "yes"]:
        turn = submit_answer(graph, "m", answer)
    assert turn["status"] == "complete"
    turn = request_corrections(graph, "m", ["lived_apart_from_spouse_all_year"])
    assert turn["field"] == "lived_apart_all_year"
    turn = submit_answer(graph, "m", "no")
    out = submit_answer(graph, "m", "yes")["output"]
    assert out["intake_record"]["lived_apart_from_spouse_all_year"] is False
    assert out["intake_corrections"][-1]["field"] == "lived_apart_all_year"


# --- Requests Intake can't satisfy never disappear ------------------------------------------


def test_unsupported_requests_are_flagged_not_dropped(graph):
    finish(graph)
    turn = request_corrections(graph, "s", ["document.r-1.7", "entered_amounts.8", "no_such_field"])
    # Nothing Intake can ask, so it doesn't make the taxpayer re-confirm an unchanged summary.
    assert turn["status"] == "complete"
    out = turn["output"]
    flagged = [e for e in out["intake_escalations"] if e["type"] == "unsupported_request"]
    assert [e["field"] for e in flagged] == ["document.r-1.7", "entered_amounts.8", "no_such_field"]
    assert "entered by hand" in flagged[0]["reason"]
    assert out["needs_human_review"] is True
    assert {r["status"] for r in out["intake_requests"].values()} == {"unsupported"}


def test_supported_and_unsupported_requests_together(graph):
    finish(graph)
    turn = request_corrections(graph, "s", ["document.r-1.7", "dual_status_alien"])
    assert turn["field"] == "dual_status_alien"
    submit_answer(graph, "s", "no")
    out = submit_answer(graph, "s", "yes")["output"]
    assert out["intake_requests"]["dual_status_alien"]["status"] == "answered"
    assert out["intake_requests"]["document.r-1.7"]["status"] == "unsupported"


def test_the_same_unsupported_request_is_flagged_once_while_open(graph):
    finish(graph)
    request_corrections(graph, "s", ["document.r-1.7"])
    out = request_corrections(graph, "s", ["document.r-1.7"])["output"]
    assert len([e for e in out["intake_escalations"] if e["type"] == "unsupported_request"]) == 1


# --- Repeated clarification requests --------------------------------------------------------


def test_a_field_asked_for_again_and_again_goes_to_a_person(graph):
    finish(graph)
    for _ in range(MAX_REQUESTS_PER_FIELD):
        turn = request_corrections(graph, "s", ["dual_status_alien"])
        assert turn["field"] == "dual_status_alien"
        submit_answer(graph, "s", "no")
        submit_answer(graph, "s", "yes")

    turn = request_corrections(graph, "s", ["dual_status_alien"])
    assert turn["status"] == "complete"  # not asked a fourth time
    out = turn["output"]
    flags = [e for e in out["intake_escalations"] if e["type"] == "repeated_request"]
    assert len(flags) == 1 and flags[0]["field"] == "dual_status_alien"
    assert out["intake_requests"]["dual_status_alien"] == {
        "requested": MAX_REQUESTS_PER_FIELD + 1, "status": "repeated", "cleared": 0}

    # Asking again while the flag is open changes nothing and adds no second flag.
    out = request_corrections(graph, "s", ["dual_status_alien"])["output"]
    assert len([e for e in out["intake_escalations"] if e["type"] == "repeated_request"]) == 1

    # A person decides on the flag: the field may be asked once more.
    decision = {"escalation_id": intake_escalation_id(flags[0]), "decision": "re_ask"}
    turn = start_or_resume(graph, "s", {"intake_reask": ["dual_status_alien"],
                                        "human_decisions": [decision]})
    assert turn["field"] == "dual_status_alien"


# --- Stopping for a person ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["I want to talk to a real person", "Can I speak with someone?", "Please connect me with a human"],
)
def test_asking_for_a_person_stops_the_interview(graph, text):
    start_or_resume(graph, "s")
    submit_answer(graph, "s", "single")
    turn = submit_answer(graph, "s", text)
    assert turn["status"] == "awaiting_human"
    assert "staff member" in turn["output"]["intake_message"]
    assert [e["type"] for e in turn["output"]["intake_escalations"]] == ["human_requested"]
    assert values(graph)["intake_answers"] == {"filing_status": "single"}


def test_exploitation_stops_the_interview_and_nothing_else_is_stored(graph):
    start_or_resume(graph, "s")
    submit_answer(graph, "s", "single")
    turn = submit_answer(graph, "s", "My nephew made me sign over my checks. 912-34-5678")
    assert turn["status"] == "awaiting_human"
    assert "taxpayer_ssn" not in values(graph)["intake_answers"]


def test_the_interview_stays_stopped_until_a_person_decides(graph):
    start_or_resume(graph, "s")
    submit_answer(graph, "s", "single")
    turn = submit_answer(graph, "s", "he keeps my social security checks")
    flag = turn["output"]["intake_escalations"][0]

    # Re-entering without a decision (a retry, or another agent's request) stays stopped.
    assert request_corrections(graph, "s", ["taxpayer_dob"])["status"] == "awaiting_human"

    # A person checks it is safe: the interview picks up again. The request that arrived while
    # it was stopped was kept, not lost, and counts once although Intake was entered twice.
    decision = {"escalation_id": intake_escalation_id(flag), "decision": "checked_safe"}
    turn = start_or_resume(graph, "s", {"human_decisions": [decision]})
    assert turn["status"] == "awaiting_answer" and turn["field"] == "taxpayer_dob"
    assert values(graph)["intake_requests"]["taxpayer_dob"] == {
        "requested": 1, "status": "pending", "cleared": 0}
    turn = submit_answer(graph, "s", "03/14/1955")
    assert turn["field"] == "taxpayer_ssn"  # then where it stopped


def test_a_new_disclosure_after_a_decision_is_a_new_flag(graph):
    start_or_resume(graph, "s")
    submit_answer(graph, "s", "single")
    flag = submit_answer(graph, "s", "he takes my money")["output"]["intake_escalations"][0]
    decision = {"escalation_id": intake_escalation_id(flag), "decision": "checked_safe"}
    start_or_resume(graph, "s", {"human_decisions": [decision]})

    turn = submit_answer(graph, "s", "he takes my money again")
    assert turn["status"] == "awaiting_human"
    flags = turn["output"]["intake_escalations"]
    assert len(flags) == 2
    assert intake_escalation_id(flags[1]) != intake_escalation_id(flags[0])  # open, not decided


def test_a_conflict_still_there_after_a_re_ask_is_raised_again(graph):
    answers = ["single", "912-34-5678", "03/14/1955", "no", "yes", "1", "Sam Doe", "son",
               "01/01/1950", "913-11-1111", "a W-2", "no", "yes"]
    turn = start_or_resume(graph, "s")
    for a in answers:
        turn = submit_answer(graph, "s", a)
    first = [e for e in turn["output"]["intake_escalations"] if e["type"] == "conflicting_information"]
    assert len(first) == 1
    decision = {"escalation_id": intake_escalation_id(first[0]), "decision": "re_ask",
                "reask_fields": ["dependent.1.dob"]}
    turn = start_or_resume(graph, "s", {"intake_reask": ["dependent.1.dob"],
                                        "human_decisions": [decision]})
    submit_answer(graph, "s", "01/01/1950")  # the same answer
    out = submit_answer(graph, "s", "yes")["output"]
    again = [e for e in out["intake_escalations"] if e["type"] == "conflicting_information"]
    assert len(again) == 2


# --- Interrupt safety -----------------------------------------------------------------------


def test_a_repeated_submission_is_not_applied_to_the_next_question(graph):
    turn = start_or_resume(graph, "s")
    qid = turn["question_id"]
    turn = submit_answer(graph, "s", "single", qid)
    assert turn["field"] == "taxpayer_ssn"
    again = submit_answer(graph, "s", "single", qid)  # double click, or a retried request
    assert again["status"] == "stale_answer" and again["field"] == "taxpayer_ssn"
    assert values(graph)["intake_answers"] == {"filing_status": "single"}
    assert len(values(graph)["intake_transcript"]) == 2


def test_question_ids_differ_when_the_same_question_is_asked_again(graph):
    first = start_or_resume(graph, "s")
    second = submit_answer(graph, "s", "not sure")
    assert first["field"] == second["field"] == "filing_status"
    assert first["question_id"] != second["question_id"]


def test_resuming_and_reading_a_waiting_session_has_no_side_effects(graph):
    start_or_resume(graph, "s")
    submit_answer(graph, "s", "single")
    submit_answer(graph, "s", "Should I file jointly?")  # one flag
    before = values(graph)
    for _ in range(3):
        start_or_resume(graph, "s")
        request_corrections(graph, "s", ["taxpayer_dob"])  # ignored: a question is waiting
    after = values(graph)
    for key in ("intake_answers", "intake_escalations", "intake_corrections", "intake_transcript"):
        assert after[key] == before[key]


def test_a_crash_after_the_answer_is_resumed_without_applying_it_twice(graph):
    start_or_resume(graph, "s")
    submit_answer(graph, "s", "single")
    submit_answer(graph, "s", "change filing status to head of household")
    # Replay the last step from its checkpoint, as LangGraph does after a crash mid-step.
    history = list(graph.get_state_history(session_config("s")))
    before_last = next(h for h in history if h.next == ("intake_ask",) and h.tasks
                       and h.tasks[0].interrupts and h.values["intake_answers"].get("filing_status") == "single"
                       and len(h.values["intake_transcript"]) == 2)
    graph.invoke(Command(resume="change filing status to head of household"), before_last.config)
    v = values(graph)
    assert v["intake_answers"]["filing_status"] == "head_of_household"
    assert len(v["intake_corrections"]) == 1


# --- Through the real Orchestrator ----------------------------------------------------------


@pytest.fixture(scope="module")
def call():
    return mcp_tool_caller()


@pytest.fixture
def full_graph(call):
    return build_graph(make_checkpointer(), extractor=FixtureW2Extractor(),
                       computation_caller=call, review_caller=call)


def _pending(g, sid="session_1"):
    pending = load_session(g, sid).pending
    return pending[0] if pending else None


def test_eligibility_questions_are_answered_and_the_return_is_approved(full_graph):
    """The loop the Orchestrator's notes describe: Eligibility asks for facts, Intake used to
    drop them, and intake -> extraction -> eligibility repeated until the loop guard fired."""
    state = session()
    del state["eligibility_facts"]
    full_graph.invoke(state, session_config("session_1"))

    asked = []
    while (q := _pending(full_graph)) and q["type"] == "intake_question":
        asked.append(q["field"])
        full_graph.invoke(Command(resume="yes" if q["field"] == "confirm" else "no"),
                          session_config("session_1"))
    v = load_session(full_graph, "session_1").values
    assert set(asked) == {"spouse_died_in_2025", "can_be_claimed_as_dependent",
                          "spouse_can_be_claimed_as_dependent", "dual_status_alien", "confirm"}
    assert v["run_status"] == "approved"
    assert v["route_history"].count("intake") == 2  # asked once, no cycle
    assert "human_review" not in v["route_history"]


def test_exploitation_in_the_full_graph_goes_to_a_preparer_before_anything_else(full_graph):
    state = session()
    del state["intake_answers"]["confirm"]
    full_graph.invoke(state, session_config("session_1"))
    assert _pending(full_graph)["field"] == "confirm"
    full_graph.invoke(Command(resume="my son forces me to give him my refund"),
                      session_config("session_1"))

    v = load_session(full_graph, "session_1").values
    assert v["route_history"] == ["intake", "human_review"]  # nothing else ran
    assert v["intake_status"] == "awaiting_human"
    packet = pending_review(full_graph, "session_1")
    assert [i["title"] for i in packet["issues"]] == [
        "Possible financial exploitation or third-party pressure"]

    submit_decisions(full_graph, "session_1", decide(packet, "stop"))
    v = load_session(full_graph, "session_1").values
    assert v["run_status"] == "handed_to_preparer"
    assert v["final_package"]["draft_return"] is None


def test_after_checked_safe_the_interview_resumes_at_the_same_question(full_graph):
    state = session()
    del state["intake_answers"]["confirm"]
    full_graph.invoke(state, session_config("session_1"))
    full_graph.invoke(Command(resume="my son forces me to give him my refund"),
                      session_config("session_1"))
    packet = pending_review(full_graph, "session_1")
    submit_decisions(full_graph, "session_1",
                     decide(packet, "checked_safe", note="Spoke with taxpayer; son is helping."))
    q = _pending(full_graph)
    assert q["type"] == "intake_question" and q["field"] == "confirm"
    full_graph.invoke(Command(resume="yes"), session_config("session_1"))
    assert load_session(full_graph, "session_1").values["run_status"] == "approved"


def test_a_request_for_a_person_in_the_full_graph_reaches_a_preparer(full_graph):
    state = session()
    del state["intake_answers"]["confirm"]
    full_graph.invoke(state, session_config("session_1"))
    full_graph.invoke(Command(resume="I'd like to talk to a person please"),
                      session_config("session_1"))
    packet = pending_review(full_graph, "session_1")
    assert [i["reason"] for i in packet["issues"]] == ["taxpayer asked to speak with a person"]
    assert load_session(full_graph, "session_1").values["route_history"] == ["intake", "human_review"]


def test_an_unsupported_request_in_the_full_graph_reaches_a_preparer(full_graph):
    state = {**session(), "intake_reask": ["document.r-1.7"]}
    full_graph.invoke(state, session_config("session_1"))
    v = load_session(full_graph, "session_1").values
    assert v["route_history"] == ["intake", "human_review"]
    packet = pending_review(full_graph, "session_1")
    assert "document.r-1.7" in packet["issues"][0]["reason"]


# --- Intake's outputs survive the trip through the main graph (state.py) ---------------------


def _keeps_asking_dual_status(state):
    """Eligibility & Rules with a fault: it asks for dual_status_alien every time, even after
    the taxpayer has answered."""
    from agents.eligibility import eligibility_node

    result = eligibility_node(state)
    return {**result, "eligibility": {**result["eligibility"], "status": "needs_facts"},
            "eligibility_reask": ["dual_status_alien"], "eligibility_notice": ""}


def _answer_intake(g, sid="session_1"):
    while (q := _pending(g, sid)) and q["type"] == "intake_question":
        g.invoke(Command(resume="yes" if q["field"] == "confirm" else "no"), session_config(sid))


def test_intake_message_reaches_the_main_state(full_graph):
    """LangGraph drops subgraph outputs the parent state doesn't declare; this one used to vanish."""
    state = session()
    del state["intake_answers"]["confirm"]
    full_graph.invoke(state, session_config("session_1"))
    full_graph.invoke(Command(resume="I'd like to talk to a person please"),
                      session_config("session_1"))
    v = load_session(full_graph, "session_1").values
    assert v["intake_status"] == "awaiting_human"
    assert "Kushim staff member" in v["intake_message"]


def test_intake_requests_reach_the_main_state(full_graph):
    full_graph.invoke({**session(), "intake_reask": ["dual_status_alien"]}, session_config("session_1"))
    _answer_intake(full_graph)
    requests = load_session(full_graph, "session_1").values["intake_requests"]
    assert requests["dual_status_alien"] == {"requested": 1, "status": "answered", "cleared": 0}


def test_a_field_requested_again_and_again_reaches_a_preparer_in_the_full_graph(
        call, monkeypatch):
    import graph as graph_module

    monkeypatch.setattr(graph_module, "eligibility_node", _keeps_asking_dual_status)
    g = build_graph(make_checkpointer(), extractor=FixtureW2Extractor(),
                    computation_caller=call, review_caller=call)
    g.invoke(session(), session_config("session_1"))
    _answer_intake(g)
    v = load_session(g, "session_1").values
    # The count carries across Intake's visits (it used to restart at 1 on every visit).
    assert v["intake_requests"]["dual_status_alien"]["requested"] == 2
    packet = pending_review(g, "session_1")
    assert [i["title"] for i in packet["issues"]] == ["Work is going round in circles"]

    # A preparer has it asked once more; the next request is one too many and goes to a person.
    submit_decisions(g, "session_1", decide(packet, "re_ask", reask_fields=["dual_status_alien"]))
    _answer_intake(g)
    v = load_session(g, "session_1").values
    assert v["intake_requests"]["dual_status_alien"]["status"] == "repeated"
    flags = [e for e in v["intake_escalations"] if e["type"] == "repeated_request"]
    assert [e["field"] for e in flags] == ["dual_status_alien"]
    assert v["route_history"][-1] == "human_review"


def test_progress_counts_the_questions_other_agents_asked_for(full_graph):
    full_graph.invoke({**session(), "intake_reask": ["dual_status_alien"]}, session_config("session_1"))
    q = _pending(full_graph)
    assert q["field"] == "dual_status_alien"
    assert q["progress"]["answered"] < q["progress"]["total"]  # it used to say "13 of 13"


def test_a_summary_of_a_half_finished_interview_doesnt_claim_answers():
    from agents.intake import summarize_answers

    text = summarize_answers({"filing_status": "single"})
    assert "Dependents: (not answered)" in text and "Estimated payments: (not answered)" in text
    full = summarize_answers({"filing_status": "single", "has_dependents": False, "has_estimated_payments": False})
    assert "Dependents: none" in full and "Estimated payments: none" in full
