"""The Intake agent (src/agents/intake.py): one question at a time, corrections, resume,
the SSN guard, escalations, and the contract with the Orchestrator."""

import json
import operator
from typing import Annotated, Any, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from agents.intake import (
    OUTPUT_KEYS,
    IntakeRecord,
    ParseError,
    build_intake_graph,
    detect_escalations,
    find_conflicts,
    parse_amount,
    parse_count,
    parse_date,
    parse_documents,
    parse_filing_status,
    parse_ssn,
    parse_yes_no,
    question_plan,
    request_corrections,
    resolve_fields,
    start_or_resume,
    submit_answer,
)
from memory import load_session, make_checkpointer, session_config

SINGLE_BASICS = ["single", "912-34-5678", "03/14/1955", "no"]
NO_DEPS_DOCS_PAYMENTS = ["no", "an SSA-1099 and a 1099-INT", "no"]


@pytest.fixture
def graph():
    return build_intake_graph(make_checkpointer())


def drive(graph, session_id, answers):
    """Start a session and answer in order. Returns every turn, starting with the first question."""
    turns = [start_or_resume(graph, session_id)]
    for answer in answers:
        assert turns[-1]["status"] == "awaiting_answer", turns[-1]
        turns.append(submit_answer(graph, session_id, answer))
    return turns


def fields_asked(turns):
    return [t["field"] for t in turns if t["status"] == "awaiting_answer"]


# --- Happy paths ---------------------------------------------------------------------------


def test_single_filer_happy_path(graph):
    turns = drive(graph, "s", SINGLE_BASICS + NO_DEPS_DOCS_PAYMENTS + ["yes"])
    assert fields_asked(turns) == [
        "filing_status",
        "taxpayer_ssn",
        "taxpayer_dob",
        "taxpayer_blind",
        "has_dependents",
        "documents",
        "has_estimated_payments",
        "confirm",
    ]
    out = turns[-1]["output"]
    assert out["intake_status"] == "complete"
    assert out["needs_human_review"] is False
    assert out["intake_escalations"] == []
    record = out["intake_record"]
    assert record["filing_status"] == "single"
    assert record["taxpayer"] == {
        "ssn": "912-34-5678",
        "date_of_birth": "1955-03-14",
        "legally_blind": False,
    }
    assert record["spouse"] is None
    assert record["dependents"] == [] and record["estimated_payments"] == []
    assert [d["type"] for d in record["documents"]] == ["SSA-1099", "1099-INT"]
    assert record["confirmed_by_taxpayer"] is True
    IntakeRecord.model_validate(record)  # the output round-trips through the schema


def test_every_turn_asks_exactly_one_question(graph):
    turns = drive(graph, "s", SINGLE_BASICS + NO_DEPS_DOCS_PAYMENTS)
    for t in turns:
        assert isinstance(t["question"], str) and t["question"]
        assert isinstance(t["field"], str)
        assert t["question"].count("?") == 1 or t["field"] == "confirm"


def test_married_joint_collects_spouse_dependents_and_payments(graph):
    turns = drive(
        graph,
        "s",
        [
            "married filing jointly",
            "912-34-5678",
            "1955-03-14",
            "no",
            "923-45-6789",
            "June 2, 1957",
            "yes",
            "yes",
            "2",
            "Ana Lopez",
            "Granddaughter",
            "05/01/2012",
            "934-56-7890",
            "Ben Lopez",
            "grandson",
            "07/04/2014",
            "945-67-8901",
            "2 W-2s and an SSA-1099",
            "yes",
            "two",
            "$1,250",
            "1250.50",
            "yes",
        ],
    )
    record = turns[-1]["output"]["intake_record"]
    assert record["spouse"] == {
        "ssn": "923-45-6789",
        "date_of_birth": "1957-06-02",
        "legally_blind": True,
    }
    assert [d["name"] for d in record["dependents"]] == ["Ana Lopez", "Ben Lopez"]
    assert record["dependents"][0]["relationship"] == "granddaughter"
    assert record["documents"][0] == {
        "type": "W-2",
        "count": 2,
        "in_scope": True,
        "description": None,
    }
    assert [p["amount"] for p in record["estimated_payments"]] == ["1250", "1250.50"]


def test_married_separately_asks_the_line_6d_question(graph):
    turns = drive(graph, "s", ["married filing separately", "yes"])
    assert fields_asked(turns)[:2] == ["filing_status", "lived_apart_all_year"]


def test_intake_never_computes_totals_or_ages(graph):
    turns = drive(graph, "s", SINGLE_BASICS + ["no", "a W-2", "yes", "2", "100", "200", "yes"])
    record = turns[-1]["output"]["intake_record"]
    text = json.dumps(record)
    assert "300" not in text  # payments recorded one by one, never summed
    assert set(record) == {
        "tax_year",
        "filing_status",
        "lived_apart_from_spouse_all_year",
        "taxpayer",
        "spouse",
        "dependents",
        "documents",
        "estimated_payments",
        "additional_facts",
        "confirmed_by_taxpayer",
    }
    assert set(record["taxpayer"]) == {"ssn", "date_of_birth", "legally_blind"}


# --- SSN guard -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "real", ["123-45-6789", "123456789", "078 05 1120", "666-12-3456", "It's 123-45-6789."]
)
def test_real_ssns_are_rejected_and_never_stored(graph, real):
    turns = drive(graph, "s", ["single", real])
    assert turns[-1]["field"] == "taxpayer_ssn"
    assert "can't accept a real Social Security number" in turns[-1]["notice"]
    stored = json.dumps(load_session(graph, "s").values)
    digits = real.replace("-", "").replace(" ", "")
    assert real not in stored and digits not in stored
    for checkpoint in graph.checkpointer.list(session_config("s")):
        assert digits not in json.dumps(checkpoint.checkpoint, default=str).replace(
            "-", ""
        ).replace(" ", "")


@pytest.mark.parametrize("bad", ["900-00-1234", "912-34-0000", "91-234-5678", "9123"])
def test_malformed_test_ssns_are_rejected(graph, bad):
    turns = drive(graph, "s", ["single", bad])
    assert turns[-1]["field"] == "taxpayer_ssn"
    assert "taxpayer_ssn" not in load_session(graph, "s").values["intake_answers"]


@pytest.mark.parametrize("ok", ["912-34-5678", "912345678", "912 34 5678", "It's 912-34-5678."])
def test_test_ssns_are_normalized(ok):
    assert parse_ssn(ok) == "912-34-5678"


def test_real_ssn_typed_at_any_question_is_rejected_and_redacted(graph):
    turns = drive(graph, "s", ["single", "912-34-5678", "my number is 123-45-6789"])
    assert turns[-1]["field"] == "taxpayer_dob"
    transcript = load_session(graph, "s").values["intake_transcript"]
    assert "123-45-6789" not in json.dumps(transcript)


def test_test_ssns_are_masked_in_the_confirmation_summary(graph):
    turns = drive(graph, "s", SINGLE_BASICS + NO_DEPS_DOCS_PAYMENTS)
    assert turns[-1]["field"] == "confirm"
    assert "912-34-5678" not in turns[-1]["question"] and "***-**-5678" in turns[-1]["question"]


# --- Corrections ---------------------------------------------------------------------------


def test_change_command_reasks_with_current_answer(graph):
    turns = drive(graph, "s", SINGLE_BASICS + ["change my date of birth"])
    assert turns[-1]["field"] == "taxpayer_dob"
    assert turns[-1]["current_answer"] == "03/14/1955"
    turns.append(submit_answer(graph, "s", "03/15/1955"))
    assert turns[-1]["field"] == "has_dependents"  # back to where we were
    values = load_session(graph, "s").values
    assert values["intake_answers"]["taxpayer_dob"] == "1955-03-15"
    assert values["intake_corrections"][0]["field"] == "taxpayer_dob"
    assert values["intake_corrections"][0]["old"] == "1955-03-14"


def test_change_with_a_new_value_applies_directly(graph):
    turns = drive(graph, "s", SINGLE_BASICS + ["change filing status to head of household"])
    assert turns[-1]["field"] == "has_dependents"
    assert "Updated filing status" in turns[-1]["notice"]
    assert load_session(graph, "s").values["intake_answers"]["filing_status"] == "head_of_household"


def test_back_reasks_the_previous_question(graph):
    turns = drive(graph, "s", ["single", "912-34-5678", "back"])
    assert turns[-1]["field"] == "taxpayer_ssn"


def test_changing_filing_status_adds_and_removes_dependent_questions(graph):
    drive(
        graph,
        "s",
        [
            "married filing jointly",
            "912-34-5678",
            "03/14/1955",
            "no",
            "923-45-6789",
            "06/02/1957",
            "no",
            "no",
            "a W-2",
            "no",
        ],
    )
    turn = submit_answer(graph, "s", "change filing status to single")
    assert turn["field"] == "confirm"
    answers = load_session(graph, "s").values["intake_answers"]
    assert not any(k.startswith("spouse") for k in answers)  # stale spouse data is dropped
    turn = submit_answer(graph, "s", "yes")
    assert turn["output"]["intake_record"]["spouse"] is None


def test_correction_at_confirmation_requires_reconfirming(graph):
    drive(graph, "s", SINGLE_BASICS + NO_DEPS_DOCS_PAYMENTS)
    turn = submit_answer(graph, "s", "documents")
    assert turn["field"] == "documents"
    turn = submit_answer(graph, "s", "a W-2 and an SSA-1099")
    assert turn["field"] == "confirm"
    assert "1 x W-2" in turn["question"]


def test_saying_no_at_confirmation_asks_what_to_change(graph):
    drive(graph, "s", SINGLE_BASICS + NO_DEPS_DOCS_PAYMENTS)
    turn = submit_answer(graph, "s", "no")
    assert turn["field"] == "confirm" and "What would you like to change" in turn["notice"]


def test_unknown_correction_target_explains_options(graph):
    turns = drive(graph, "s", ["single", "change the weather"])
    assert "not sure which answer" in turns[-1]["notice"]


def test_correction_for_a_question_that_does_not_apply(graph):
    turns = drive(graph, "s", SINGLE_BASICS + ["change my spouse's date of birth"])
    assert turns[-1]["field"] == "has_dependents"
    assert "not sure which answer" in turns[-1]["notice"]


@pytest.mark.parametrize(
    "phrase, fields",
    [
        ("my spouse's date of birth", ["spouse_dob"]),
        ("date of birth", ["taxpayer_dob"]),
        (
            "dependent 2",
            ["dependent.2.name", "dependent.2.relationship", "dependent.2.dob", "dependent.2.ssn"],
        ),
        ("dependent #1 birthday", ["dependent.1.dob"]),
        ("payment 2", ["estimated_payment.2.amount"]),
        ("filing status", ["filing_status"]),
        ("number of dependents", ["dependent_count"]),
        ("my documents", ["documents"]),
        ("wife", ["spouse_ssn", "spouse_dob", "spouse_blind"]),
        ("blindness", ["taxpayer_blind"]),
    ],
)
def test_resolve_fields(phrase, fields):
    assert resolve_fields(phrase, {}) == fields


# --- Resume --------------------------------------------------------------------------------


def test_interrupted_session_resumes_at_the_same_question(graph):
    drive(graph, "s", ["single", "912-34-5678"])
    turn = start_or_resume(graph, "s")  # the taxpayer comes back later
    assert turn["field"] == "taxpayer_dob"
    assert "Welcome back" in turn["notice"]
    assert turn["progress"] == {"answered": 2, "total": 7}


def test_pause_keeps_progress(graph):
    turns = drive(graph, "s", ["single", "pause"])
    assert turns[-1]["field"] == "taxpayer_ssn" and "saved" in turns[-1]["notice"]


def test_completed_session_returns_its_output(graph):
    drive(graph, "s", SINGLE_BASICS + NO_DEPS_DOCS_PAYMENTS + ["yes"])
    turn = start_or_resume(graph, "s")
    assert (
        turn["status"] == "complete"
        and turn["output"]["intake_record"]["filing_status"] == "single"
    )


def test_submit_answer_requires_a_waiting_question(graph):
    with pytest.raises(RuntimeError):
        submit_answer(graph, "never-started", "single")


# --- Escalations ---------------------------------------------------------------------------


def _escalations(graph, sid="s"):
    return load_session(graph, sid).values["intake_escalations"]


def test_tax_advice_is_flagged_not_answered(graph):
    turns = drive(graph, "s", ["Which filing status is better for me?"])
    assert turns[-1]["field"] == "filing_status"  # same question again
    assert "not able to give tax advice" in turns[-1]["notice"]
    assert [e["type"] for e in _escalations(graph)] == ["tax_advice_request"]
    assert "filing_status" not in load_session(graph, "s").values["intake_answers"]


def test_out_of_scope_situation_is_flagged(graph):
    turns = drive(graph, "s", SINGLE_BASICS + ["no, but I have rental income from a condo"])
    assert turns[-1]["field"] == "documents"  # the answer still counts
    assert _escalations(graph)[0]["type"] == "out_of_scope"
    assert _escalations(graph)[0]["reason"] == "rental income"


def test_out_of_scope_documents_are_recorded_and_flagged(graph):
    turns = drive(graph, "s", SINGLE_BASICS + ["no", "a W-2 and a 1099-NEC", "no", "yes"])
    out = turns[-1]["output"]
    nec = [d for d in out["intake_record"]["documents"] if d["type"] == "1099-NEC"][0]
    assert nec["in_scope"] is False
    assert out["needs_human_review"] is True
    assert out["intake_escalations"][0]["reason"] == "a 1099-NEC"


def test_financial_exploitation_is_flagged_and_stops_the_interview(graph):
    turns = drive(
        graph,
        "s",
        [
            "single",
            "My grandson takes my social security checks and told me to send him gift cards",
        ],
    )
    assert turns[-1]["status"] == "awaiting_human"  # no further questions
    assert "Elder Fraud Hotline" in turns[-1]["output"]["intake_message"]
    assert "financial_exploitation" in [e["type"] for e in _escalations(graph)]


def test_escalations_are_not_duplicated(graph):
    drive(graph, "s", ["Should I file jointly?", "Should I file jointly?"])
    assert len(_escalations(graph)) == 1


def test_conflicting_information_is_flagged_at_the_end(graph):
    turns = drive(
        graph,
        "s",
        [
            "single",
            "912-34-5678",
            "03/14/1955",
            "no",
            "yes",
            "1",
            "Sam Doe",
            "son",
            "01/01/1950",
            "912-34-5678",
            "a W-2",
            "no",
        ],
    )
    assert "Please double-check" in turns[-1]["question"]
    out = submit_answer(graph, "s", "yes")["output"]
    reasons = [
        e["reason"] for e in out["intake_escalations"] if e["type"] == "conflicting_information"
    ]
    assert any("same SSN" in r for r in reasons)
    assert any("born before the taxpayer" in r for r in reasons)
    assert out["needs_human_review"] is True


@pytest.mark.parametrize(
    "text, kind",
    [
        ("Should I itemize?", "tax_advice_request"),
        ("how much will I owe?", "tax_advice_request"),
        ("can I deduct my medical bills", "tax_advice_request"),
        ("I sold my house this year", "out_of_scope"),
        ("I'm self-employed", "out_of_scope"),
        ("my husband passed away in March", "out_of_scope"),
        ("my daughter claims me as a dependent", "out_of_scope"),
        (
            "someone from the IRS called and said I'd be arrested unless I pay",
            "financial_exploitation",
        ),
        ("my caretaker won't let me see my bank statements", "financial_exploitation"),
    ],
)
def test_detect_escalations(text, kind):
    assert kind in [k for k, _ in detect_escalations(text)]


@pytest.mark.parametrize(
    "text", ["yes", "no", "single", "03/14/1955", "I have high blood pressure", "two W-2s"]
)
def test_ordinary_answers_are_not_escalated(text):
    assert detect_escalations(text) == []


# --- Parsers -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Single", "single"),
        ("married filing jointly", "married_filing_jointly"),
        ("MFJ", "married_filing_jointly"),
        ("we file separately", "married_filing_separately"),
        ("head of household", "head_of_household"),
        ("qualifying surviving spouse", "qualifying_surviving_spouse"),
        ("widow", "qualifying_surviving_spouse"),
    ],
)
def test_parse_filing_status(text, expected):
    assert parse_filing_status(text) == expected


def test_married_alone_is_ambiguous():
    with pytest.raises(ParseError, match="jointly"):
        parse_filing_status("married")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("yes", True),
        ("Y", True),
        ("Yes, I am", True),
        ("no", False),
        ("Nope", False),
        ("I'm not", False),
        ("I am", True),
    ],
)
def test_parse_yes_no(text, expected):
    assert parse_yes_no(text) is expected


@pytest.mark.parametrize("text", ["not sure", "I don't know", "purple"])
def test_parse_yes_no_rejects_unclear_answers(text):
    with pytest.raises(ParseError):
        parse_yes_no(text)


@pytest.mark.parametrize(
    "text",
    [
        "03/14/1955",
        "3/14/1955",
        "1955-03-14",
        "March 14, 1955",
        "Mar 14 1955",
        "14 March 1955",
        "March 14th, 1955",
    ],
)
def test_parse_date_formats(text):
    assert parse_date(text) == "1955-03-14"


@pytest.mark.parametrize("text", ["3/14/55", "yesterday", "02/30/1955", "01/01/2026", "01/01/1899"])
def test_parse_date_rejects(text):
    with pytest.raises(ParseError):
        parse_date(text)


@pytest.mark.parametrize(
    "text, expected", [("$1,250.00", "1250.00"), ("1250", "1250"), ("1,250.5 dollars", "1250.5")]
)
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize("text", ["-5", "0", "12.345", "1,25", "a lot"])
def test_parse_amount_rejects(text):
    with pytest.raises(ParseError):
        parse_amount(text)


@pytest.mark.parametrize("text, expected", [("2", 2), ("two", 2), ("I have 3", 3)])
def test_parse_count(text, expected):
    assert parse_count(text, 10) == expected


@pytest.mark.parametrize("text", ["0", "none", "11", "lots"])
def test_parse_count_rejects(text):
    with pytest.raises(ParseError):
        parse_count(text, 10)


def test_parse_documents():
    docs = parse_documents("2 W-2s, an SSA-1099, one 1099-INT, a 1099-R and a 1098")
    assert docs == [
        {"type": "W-2", "count": 2},
        {"type": "SSA-1099", "count": 1},
        {"type": "1099-INT", "count": 1},
        {"type": "1099-R", "count": 1},
        {"type": "1098", "count": 1},
    ]


def test_parse_documents_none_and_other():
    assert parse_documents("none") == []
    assert parse_documents("other: a letter from my pension plan") == [
        {"type": "other", "count": 1, "description": "a letter from my pension plan"}
    ]


def test_parse_documents_asks_which_1099():
    with pytest.raises(ParseError, match="Which kind of 1099"):
        parse_documents("some 1099s")


def test_question_plan_is_derived_from_answers():
    assert "spouse_ssn" not in question_plan({"filing_status": "single"})
    assert "spouse_ssn" in question_plan({"filing_status": "married_filing_jointly"})
    plan = question_plan({"has_dependents": True, "dependent_count": 2})
    assert "dependent.2.ssn" in plan and "dependent.3.name" not in plan


def test_find_conflicts_ignores_non_child_relationships():
    answers = {
        "filing_status": "single",
        "taxpayer_dob": "1955-01-01",
        "has_dependents": True,
        "dependent_count": 1,
        "dependent.1.relationship": "mother",
        "dependent.1.dob": "1930-01-01",
    }
    assert find_conflicts(answers) == []


# --- Orchestrator integration --------------------------------------------------------------


def test_orchestrator_can_reask_fields_after_completion(graph):
    drive(graph, "s", SINGLE_BASICS + NO_DEPS_DOCS_PAYMENTS + ["yes"])
    turn = request_corrections(
        graph, "s", ["taxpayer_dob"], "Your SSA-1099 shows a different birth date."
    )
    assert turn["field"] == "taxpayer_dob"
    assert turn["notice"] == "Your SSA-1099 shows a different birth date."
    turn = submit_answer(graph, "s", "03/15/1955")
    assert turn["field"] == "confirm"  # changed answers must be confirmed again
    out = submit_answer(graph, "s", "yes")["output"]
    assert out["intake_record"]["taxpayer"]["date_of_birth"] == "1955-03-15"
    assert out["intake_corrections"][-1]["field"] == "taxpayer_dob"


class ParentState(TypedDict, total=False):
    """A stand-in for the shared state in src/state.py, with the keys Intake needs."""

    session_id: str
    intake_status: str
    intake_record: dict[str, Any] | None
    intake_answers: dict[str, Any]
    intake_corrections: list
    intake_escalations: list
    intake_reask: list[str]
    intake_notice: str
    intake_requests: dict[str, Any]  # proposed for src/state.py
    intake_message: str  # proposed for src/state.py
    human_decisions: list
    needs_human_review: bool
    trail: Annotated[list[str], operator.add]


def test_intake_runs_as_a_subgraph_of_the_orchestrator():
    def after_intake(state):
        return {"trail": [f"orchestrator saw {state['intake_record']['filing_status']}"]}

    parent = StateGraph(ParentState)
    parent.add_node("intake", build_intake_graph())  # no checkpointer: inherits the parent's
    parent.add_node("next_agent", after_intake)
    parent.add_edge(START, "intake")
    parent.add_edge("intake", "next_agent")
    parent.add_edge("next_agent", END)
    app = parent.compile(checkpointer=make_checkpointer())
    config = session_config("p1")

    result = app.invoke({"session_id": "p1"}, config)
    for answer in SINGLE_BASICS + NO_DEPS_DOCS_PAYMENTS + ["yes"]:
        assert result["__interrupt__"][0].value["type"] == "intake_question"
        result = app.invoke(Command(resume=answer), config)

    assert result["intake_status"] == "complete"
    assert result["intake_record"]["taxpayer"]["date_of_birth"] == "1955-03-14"
    assert result["trail"] == ["orchestrator saw single"]
    assert "intake_transcript" not in result  # internal to Intake
    assert set(OUTPUT_KEYS) <= set(result)
