"""Tests of the human review step, where a preparer decides on escalations (specification
section 4.4).

What is tested:
- Each kind of issue offers exactly the options in the escalation table, and anything
  unrecognised offers only "re-ask" or "stop".
- "Checked: safe to continue" is refused unless the preparer says why.
- An answer that skips an issue, picks an option that isn't allowed, names an unknown issue or
  gives no name is refused, and nothing is recorded.
- "Re-ask" only accepts questions Intake can actually ask.
- A filing status chosen by the preparer is recorded in the form Eligibility & Rules reads.
- Through the full graph: a chosen filing status finishes the draft; "stop" ends the run with
  a handoff package; a noted tax-advice question lets the draft finish; "re-ask" pauses for the
  taxpayer with the preparer's note shown before the question."""

import pytest

from agents.eligibility.facts import HumanResolution
from agents.intake import make_escalation
from agents.orchestrator import pending_review, submit_decisions
from agents.orchestrator.human_review import _issue, check_answer, options_for, record_decisions
from memory import load_session

from .scenarios import decide, run, session


def _intake_issue(kind: str, reason: str, text: str) -> dict:
    record = make_escalation(kind, reason, "filing_status", text)
    return _issue({"id": f"intake:{kind}:{reason}", "source_agent": "intake", "record": record})


@pytest.mark.parametrize("agent, record, expected", [
    ("intake", {"type": "tax_advice_request"}, ["noted", "stop"]),
    ("intake", {"type": "financial_exploitation"}, ["checked_safe", "stop"]),
    ("intake", {"type": "out_of_scope", "field": "filing_status"}, ["dismiss", "stop"]),
    ("intake", {"type": "out_of_scope", "field": "documents"}, ["re_ask", "stop"]),
    ("intake", {"type": "conflicting_information"}, ["re_ask", "stop"]),
    ("eligibility_rules", {"category": "filing_status_ambiguity"}, ["choose_filing_status", "re_ask", "stop"]),
    ("eligibility_rules", {"category": "user_document_conflict"}, ["accept_documents", "re_ask", "stop"]),
    ("eligibility_rules", {"category": "taxable_amount_not_determined"}, ["stop"]),
    ("eligibility_rules", {"category": "out_of_scope"}, ["re_ask", "stop"]),
    ("eligibility_rules", {"category": "security"}, ["re_ask", "stop"]),
    ("document_extraction", {"code": "unreadable_box"}, ["re_read", "stop"]),
    ("document_extraction", {"code": "wrong_tax_year"}, ["re_ask", "stop"]),
    ("review", {"code": "review_iteration_limit"}, ["re_ask", "stop"]),
    ("orchestrator", {"code": "handoff_cycle"}, ["re_ask", "stop"]),
    ("someone_new", {"code": "anything"}, ["re_ask", "stop"]),
])
def test_each_issue_offers_the_options_in_the_table(agent, record, expected):
    assert options_for(agent, record) == expected


def test_safe_to_continue_needs_a_reason():
    issue = _intake_issue("financial_exploitation", "possible financial exploitation",
                          "My nephew says to send the refund to his account")
    answer = {"decided_by": "preparer_test",
              "decisions": [{"escalation_id": issue["escalation_id"], "decision": "checked_safe"}]}
    _, problems = check_answer(answer, [issue], [])
    assert problems == ['Issue 1: "Checked: safe to continue" needs a note saying why it is safe to continue.']

    answer["decisions"][0]["note"] = "Spoke to the taxpayer alone; the nephew is their named agent."
    decisions, problems = check_answer(answer, [issue], [])
    assert problems == [] and decisions[0]["note"].startswith("Spoke to the taxpayer")


def test_an_answer_must_cover_every_issue_with_an_allowed_option():
    advice = _intake_issue("tax_advice_request", "taxpayer asked for tax advice", "Should we file separately?")
    exploit = _intake_issue("financial_exploitation", "possible financial exploitation", "gift cards")
    answer = {"decided_by": "",
              "decisions": [{"escalation_id": advice["escalation_id"], "decision": "dismiss"},
                            {"escalation_id": "made-up", "decision": "stop"}]}
    _, problems = check_answer(answer, [advice, exploit], [])
    assert len(problems) == 4  # no name, unknown id, option not allowed, missing decision
    assert check_answer("stop", [advice], [])[1] == ["The answer must give a name and one decision per issue."]


def test_re_ask_accepts_only_questions_intake_has():
    issue = _intake_issue("conflicting_information", "the same SSN was given twice", "")
    answer = {"decided_by": "preparer_test", "decisions": [
        {"escalation_id": issue["escalation_id"], "decision": "re_ask", "reask_fields": ["spouse_died_in_2025"]}]}
    _, problems = check_answer(answer, [issue], ["taxpayer_ssn", "spouse_ssn"])
    assert problems == ["Issue 1: name at least one question Intake can ask (not on Intake's list: spouse_died_in_2025)."]


def test_a_recorded_filing_status_is_one_eligibility_accepts():
    record = {"source_agent": "eligibility_rules", "document_id": None, "code": "spouse_died_in_tax_year",
              "category": "filing_status_ambiguity", "reason": "...", "attempt": 1}
    issue = _issue({"id": "eligibility_rules:spouse_died_in_tax_year:-:1", "source_agent": "eligibility_rules",
                    "record": record})
    decisions, problems = check_answer(decide({"issues": [issue]}, "choose_filing_status",
                                              filing_status="married_filing_jointly", note="Died in March"),
                                       [issue], [])
    assert problems == []
    update = record_decisions([issue], decisions)
    resolution = HumanResolution.model_validate(update["human_resolutions"][0])
    assert resolution.code == "spouse_died_in_tax_year" and resolution.filing_status.value == "married_filing_jointly"
    assert update["escalations"][0]["resolved"] is True
    assert update["human_decisions"][0]["decided_by"] == "preparer_test"


# --- Through the full graph ---------------------------------------------------------------------


def _spouse_died():
    state = session()
    state["eligibility_facts"]["spouse_died_in_2025"] = True
    return state


def test_spouse_died_pauses_and_a_chosen_filing_status_finishes_the_draft(graph):
    snap = run(graph, _spouse_died())
    assert snap.values["run_status"] == "awaiting_human"
    packet = pending_review(graph, "session_1")
    assert packet["type"] == "human_review" and "ISSUE 1 of 1: Filing status unclear" in packet["packet"]

    submit_decisions(graph, "session_1", decide(packet, "choose_filing_status",
                                                filing_status="married_filing_jointly", note="Died in March"))
    v = load_session(graph, "session_1").values
    assert v["run_status"] == "approved"
    finding = next(f for f in v["eligibility"]["findings"] if f["topic"] == "filing_status")
    assert finding["decided_by"] == "human"


def test_a_bad_answer_pauses_again_and_records_nothing(graph):
    run(graph, _spouse_died())
    packet = submit_decisions(graph, "session_1", {"decided_by": "preparer_test", "decisions": []})
    assert packet["problems"] == ["Issue 1 (Filing status unclear) has no decision."]
    assert load_session(graph, "session_1").values.get("human_decisions", []) == []


def test_stop_hands_the_run_to_a_preparer(graph):
    state = session()
    state["entered_documents"][3]["boxes"]["2b_taxable_not_determined"] = True
    run(graph, state)
    packet = pending_review(graph, "session_1")
    assert [o["decision"] for o in packet["issues"][0]["options"]] == ["stop"]

    submit_decisions(graph, "session_1", decide(packet, "stop", note="Needs the Simplified Method"))
    v = load_session(graph, "session_1").values
    assert v["run_status"] == "handed_to_preparer"
    assert v["final_package"]["stop_reasons"][0]["note"] == "Needs the Simplified Method"
    assert v["task_ledger"]["computation"]["status"] == "stopped"


def test_a_noted_advice_question_lets_the_draft_finish(graph):
    state = session()
    state["intake_escalations"] = [make_escalation("tax_advice_request", "taxpayer asked for tax advice",
                                                   "filing_status", "Would we pay less if we filed separately?")]
    run(graph, state)
    packet = pending_review(graph, "session_1")
    assert packet["issues"][0]["taxpayer_said"] == "Would we pay less if we filed separately?"

    submit_decisions(graph, "session_1", decide(packet, "noted", note="Will call them"))
    v = load_session(graph, "session_1").values
    assert v["run_status"] == "approved"
    assert v["preparer_notes"][0]["taxpayer_said"] == "Would we pay less if we filed separately?"


def test_re_ask_sends_the_question_back_to_the_taxpayer(graph):
    state = session()
    state["intake_answers"]["documents"][0]["count"] = 3  # said three W-2s, supplied two
    run(graph, state)
    packet = pending_review(graph, "session_1")
    assert packet["issues"][0]["title"] == "An answer doesn't match a document"

    submit_decisions(graph, "session_1", decide(packet, "re_ask", reask_fields=["documents"],
                                                note="We received two W-2s. Please check how many you have."))
    snap = load_session(graph, "session_1")
    question = snap.pending[0]
    assert question["type"] == "intake_question" and question["field"] == "documents"
    assert question["notice"] == "We received two W-2s. Please check how many you have."
