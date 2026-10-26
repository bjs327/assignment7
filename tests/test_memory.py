"""Agent memory: checkpointing, session lookup, durable resume, redaction and the
context-window strategy (src/memory.py)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from agents.intake import build_intake_graph, start_or_resume, submit_answer
from memory import (
    SSN_PLACEHOLDER,
    build_context,
    contains_ssn,
    estimate_tokens,
    list_sessions,
    load_session,
    make_checkpointer,
    redact_ssns,
    session_config,
)


def test_default_checkpointer_is_in_memory():
    assert isinstance(make_checkpointer(), InMemorySaver)


def test_path_gives_a_sqlite_checkpointer(tmp_path):
    assert isinstance(make_checkpointer(tmp_path / "s.sqlite"), SqliteSaver)


def test_session_config_uses_the_session_id_as_thread_id():
    assert session_config("taxpayer_42") == {"configurable": {"thread_id": "taxpayer_42"}}


@pytest.mark.parametrize("bad", ["", "has space", "x" * 129, "client-912-34-5678", "123456789"])
def test_session_config_rejects_bad_ids_and_ssns(bad):
    with pytest.raises(ValueError):
        session_config(bad)


def test_load_session_for_unknown_session():
    graph = build_intake_graph(make_checkpointer())
    snap = load_session(graph, "nobody")
    assert not snap.exists and not snap.pending and not snap.is_finished


def test_load_session_reports_the_waiting_question():
    graph = build_intake_graph(make_checkpointer())
    start_or_resume(graph, "s1")
    snap = load_session(graph, "s1")
    assert snap.exists and snap.is_waiting_for_input
    assert snap.pending[0]["field"] == "filing_status"


def test_list_sessions():
    saver = make_checkpointer()
    graph = build_intake_graph(saver)
    for sid in ("b", "a"):
        start_or_resume(graph, sid)
    assert list_sessions(saver) == ["a", "b"]


def test_session_survives_a_process_restart(tmp_path):
    """Tuesday: answer two questions and close. Friday: a new process resumes at question three."""
    db = tmp_path / "sessions.sqlite"
    tuesday = build_intake_graph(make_checkpointer(db))
    start_or_resume(tuesday, "t1")
    submit_answer(tuesday, "t1", "single")
    submit_answer(tuesday, "t1", "912-34-5678")
    del tuesday

    friday = build_intake_graph(make_checkpointer(db))
    turn = start_or_resume(friday, "t1")
    assert turn["field"] == "taxpayer_dob"
    assert "Welcome back" in turn["notice"]
    snap = load_session(friday, "t1")
    assert snap.values["intake_answers"] == {
        "filing_status": "single",
        "taxpayer_ssn": "912-34-5678",
    }


def test_sessions_are_isolated():
    graph = build_intake_graph(make_checkpointer())
    start_or_resume(graph, "a")
    start_or_resume(graph, "b")
    submit_answer(graph, "a", "single")
    assert load_session(graph, "b").values["intake_answers"] == {}


# --- Redaction -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "123-45-6789",
        "my ssn is 123 45 6789",
        "123456789",
        "912-34-5678",
        "It's 123-45-6789.",
        "123-45-6789, thanks",
        "(123-45-6789)",
    ],
)
def test_redacts_ssn_formats(text):
    assert contains_ssn(text)
    assert SSN_PLACEHOLDER in redact_ssns(text)
    assert not contains_ssn(redact_ssns(text))


@pytest.mark.parametrize(
    "text", ["03/14/1955", "1955-03-14", "$1,250.00", "1234567890", "555-123-4567"]
)
def test_does_not_redact_dates_amounts_or_longer_numbers(text):
    assert redact_ssns(text) == text


def test_redacts_nested_structures():
    out = redact_ssns({"a": ["x 123-45-6789"], "b": {"c": "123456789"}, "n": 5})
    assert out == {"a": [f"x {SSN_PLACEHOLDER}"], "b": {"c": SSN_PLACEHOLDER}, "n": 5}


@given(st.from_regex(r"\A[0-9]{3}-[0-9]{2}-[0-9]{4}\Z"), st.text(alphabet="abc .", max_size=20))
def test_redaction_never_leaks_an_ssn(ssn, filler):
    assert ssn not in redact_ssns(f"{filler} {ssn} {filler}")


# --- Context-window strategy ---------------------------------------------------------------


def _transcript(n):
    return [{"role": "user", "content": f"answer {i}"} for i in range(n)]


def test_context_keeps_only_recent_turns():
    ctx = build_context({"filing_status": "single"}, _transcript(20), max_recent_turns=4)
    assert [t["content"] for t in ctx["recent_turns"]] == [
        "answer 16",
        "answer 17",
        "answer 18",
        "answer 19",
    ]
    assert ctx["omitted_turns"] == 16
    assert ctx["facts"] == {"filing_status": "single"}


def test_context_strategy_can_be_disabled():
    ctx = build_context({}, _transcript(20), max_recent_turns=None)
    assert len(ctx["recent_turns"]) == 20 and ctx["omitted_turns"] == 0


def test_context_redacts_ssns_and_lists_escalations():
    ctx = build_context(
        {"taxpayer_ssn": "912-34-5678"},
        [{"role": "user", "content": "it's 123-45-6789"}],
        [{"type": "out_of_scope", "reason": "rental income", "excerpt": "..."}],
    )
    assert "912-34-5678" not in str(ctx) and "123-45-6789" not in str(ctx)
    assert ctx["open_escalations"] == [{"type": "out_of_scope", "reason": "rental income"}]


def test_context_size_stays_flat_with_the_strategy_on():
    facts = {"filing_status": "single"}
    sizes_on = [estimate_tokens(build_context(facts, _transcript(n))) for n in (10, 50, 200)]
    sizes_off = [
        estimate_tokens(build_context(facts, _transcript(n), max_recent_turns=None))
        for n in (10, 50, 200)
    ]
    assert sizes_on[-1] - sizes_on[0] < 5
    assert sizes_off[-1] > 10 * sizes_off[0] / 2


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens({"a": 1}) > 0
