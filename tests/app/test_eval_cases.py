"""The two evaluation cases of specification section 5.2, run from the files in demo/ exactly as
demo/eval_case_1.md and demo/eval_case_2.md tell a grader to: the W-2 image, the typed-in forms
and the interview answers, through every agent. Plus session behaviour the cases depend on:
resuming in a new process and ignoring a repeated answer.

Uses the offline W-2 reader (the ground truth stored next to demo/w2_image_1.jpg) and the real
tax_math tools, so no API key is needed."""

import json

import pytest

from agents.orchestrator import pending_review, submit_decisions
from app import session as sessions
from app.cli import main as run_session
from app.documents import load_forms_file, register_path
from app.form1040sr import build_form, render_html

from ..computation.scenarios import MFJ_EXPECTED
from ..orchestrator.scenarios import ROOT

DEMO = ROOT / "demo"


def _start(graph, sid, tmp_path):
    documents = [register_path(sid, DEMO / "w2_image_1.jpg", [], upload_root=tmp_path)]
    entered, amounts = load_forms_file(json.loads((DEMO / "eval_case_1_forms.json").read_text()))
    return sessions.start(graph, sid, documents=documents, entered_documents=entered, entered_amounts=amounts)


def _answer_all(graph, sid, s, answers):
    answers = list(answers)
    while s["state"] == "question":
        s = sessions.answer(graph, sid, s["question"]["question_id"], answers.pop(0))
    assert answers == [], f"answers left over: {answers}"
    return s


def _lines(form):
    return {r["line"]: r["value"] for sec in form["sections"] for r in sec["rows"] if r["value"] is not None}


@pytest.fixture
def graph(tmp_path):
    return sessions.open_graph(tmp_path / "sessions.sqlite", offline=True)


def test_eval_case_1_clean_run(graph, tmp_path):
    s = _start(graph, "eval1", tmp_path)
    s = _answer_all(graph, "eval1", s, (DEMO / "eval_case_1_answers.txt").read_text().splitlines())
    assert s["state"] == "finished" and s["run_status"] == "approved"
    v = s["values"]
    assert v["route_history"] == ["intake", "extraction", "eligibility_rules", "intake", "extraction",
                                  "eligibility_rules", "computation", "review", "finalize"]
    assert "human_review" not in v["route_history"]
    form = build_form(s["package"])
    assert {k: v for k, v in _lines(form).items() if k in MFJ_EXPECTED} == MFJ_EXPECTED  # worked by hand
    # Every amount on the form has a record of where it came from.
    for sec in form["sections"]:
        for row in sec["rows"]:
            if row["value"] is not None:
                x = row["explanation"]
                assert x["tool"] and x["citations"], row["line"]
    line_1z = next(r for sec in form["sections"] for r in sec["rows"] if r["line"] == "1z")
    assert any("read from the uploaded image" in s for s in line_1z["explanation"]["sources"])
    line_6a = next(r for sec in form["sections"] for r in sec["rows"] if r["line"] == "6a")
    assert all("direct user input, typed in by taxpayer" in s for s in line_6a["explanation"]["sources"])
    page = render_html(form)
    assert page.count("<details>") == len(_lines(form)) and "DRAFT" in page


def test_eval_case_2_hitl_is_resolved_by_a_preparer(graph, tmp_path):
    s = _start(graph, "eval2", tmp_path)
    s = _answer_all(graph, "eval2", s, (DEMO / "eval_case_2_answers.txt").read_text().splitlines())
    assert s["state"] == "preparer"
    packet = pending_review(graph, "eval2")
    [issue] = packet["issues"]
    assert issue["title"] == "Filing status unclear"
    assert [o["decision"] for o in issue["options"]] == ["choose_filing_status", "re_ask", "stop"]
    assert any(r.startswith("FS-06") for r in issue["rules"])

    # A refused answer records nothing and pauses again with the problem listed.
    again = submit_decisions(graph, "eval2", {"decided_by": "", "decisions": []})
    assert again["problems"] and not sessions.status(graph, "eval2")["values"].get("human_decisions")

    submit_decisions(graph, "eval2", {"decided_by": "Preparer Pat", "decisions": [{
        "escalation_id": issue["escalation_id"], "decision": "choose_filing_status",
        "filing_status": "married_filing_jointly", "note": "Spouse died in 2025: joint return allowed."}]})
    s = sessions.status(graph, "eval2")
    assert s["state"] == "finished" and s["run_status"] == "approved"
    form = build_form(s["package"])
    assert _lines(form)["12e"] == 34700 and form["filing_status"] == "Married filing jointly"
    assert [d["decided_by"] for d in form["human_decisions"]] == ["Preparer Pat"]


def test_eval_case_2_stop_hands_the_return_to_a_preparer(graph, tmp_path):
    s = _start(graph, "eval2s", tmp_path)
    _answer_all(graph, "eval2s", s, (DEMO / "eval_case_2_answers.txt").read_text().splitlines())
    issue = pending_review(graph, "eval2s")["issues"][0]
    submit_decisions(graph, "eval2s", {"decided_by": "Preparer Pat", "decisions": [
        {"escalation_id": issue["escalation_id"], "decision": "stop", "note": "Will call the client."}]})
    s = sessions.status(graph, "eval2s")
    assert s["run_status"] == "handed_to_preparer"
    form = build_form(s["package"])
    assert not form["has_draft"] and "No draft return was produced" in render_html(form)


def test_a_session_resumes_in_a_new_process_with_a_summary(tmp_path):
    db = tmp_path / "sessions.sqlite"
    first = sessions.open_graph(db, offline=True)
    s = _start(first, "resume1", tmp_path)
    answers = (DEMO / "eval_case_1_answers.txt").read_text().splitlines()
    for a in answers[:3]:
        s = sessions.answer(first, "resume1", s["question"]["question_id"], a)

    second = sessions.open_graph(db, offline=True)  # as if the window was closed and reopened later
    s = sessions.status(second, "resume1")
    assert s["state"] == "question" and s["question"]["field"] == "taxpayer_blind"
    welcome = sessions.welcome_back(s)
    assert "answered 3 of" in welcome and "Married filing jointly" in welcome
    assert "900-12-3456" not in welcome and "***-**-3456" in welcome  # the SSN is masked
    s = _answer_all(second, "resume1", s, answers[3:])
    assert s["run_status"] == "approved"


def test_a_repeated_answer_is_not_applied_to_the_next_question(graph, tmp_path):
    s = _start(graph, "dup1", tmp_path)
    qid = s["question"]["question_id"]
    s = sessions.answer(graph, "dup1", qid, "married filing jointly")
    again = sessions.answer(graph, "dup1", qid, "married filing jointly")  # a double click
    assert again.get("ignored") and again["question"]["field"] == "taxpayer_ssn"


def test_starting_an_existing_session_again_changes_nothing(graph, tmp_path):
    s = _start(graph, "dup2", tmp_path)
    again = sessions.start(graph, "dup2", documents=[])
    assert again["question"]["question_id"] == s["question"]["question_id"]
    assert [d["document_id"] for d in again["values"]["documents"]] == ["w2-1"]


def test_the_terminal_program_runs_eval_case_1_and_writes_the_form(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("app.documents.UPLOAD_ROOT", tmp_path / "uploads")
    out = tmp_path / "form.html"
    run_session(["--session", "cli1", "--w2", str(DEMO / "w2_image_1.jpg"), "--forms",
                 str(DEMO / "eval_case_1_forms.json"), "--offline", "--db", str(tmp_path / "s.sqlite"),
                 "--answers", str(DEMO / "eval_case_1_answers.txt"), "--out", str(out)])
    assert "Run status: approved" in capsys.readouterr().out
    assert "Form 1040-SR" in out.read_text()


def _file_contains(path, text):
    """Search every row of every table through SQLite. (Reading the file's bytes would miss rows
    still in the write-ahead log.)"""
    import sqlite3

    con = sqlite3.connect(path)
    try:
        tables = [r[0] for r in con.execute("select name from sqlite_master where type = 'table'")]
        return any(text.encode() in (c if isinstance(c, bytes) else str(c).encode())
                   for t in tables for row in con.execute(f"select * from {t}") for c in row)
    finally:
        con.close()


def test_a_real_looking_ssn_never_reaches_the_saved_session(tmp_path):
    """It used to be refused inside the graph, after LangGraph had already saved the answer in
    the SQLite file's writes table, while the taxpayer was told it wasn't saved."""
    db = tmp_path / "sessions.sqlite"
    graph = sessions.open_graph(db, offline=True)
    s = sessions.start(graph, "pii1")
    s = sessions.answer(graph, "pii1", s["question"]["question_id"], "single")
    s = sessions.answer(graph, "pii1", s["question"]["question_id"], "my number is 123-45-6789")
    assert s.get("refused") and "can't accept a real Social Security number" in s["question"]["notice"]
    assert s["question"]["field"] == "taxpayer_ssn"  # asked again
    s = sessions.answer(graph, "pii1", s["question"]["question_id"], "900-12-3456")
    assert s["question"]["field"] == "taxpayer_dob"
    assert not _file_contains(db, "123-45-6789") and not _file_contains(db, "123456789")


def test_the_standalone_interview_refuses_it_before_the_graph_too(tmp_path):
    from agents.intake import build_intake_graph, start_or_resume, submit_answer
    from memory import make_checkpointer

    db = tmp_path / "intake.sqlite"
    graph = build_intake_graph(make_checkpointer(db))
    turn = start_or_resume(graph, "pii2")
    turn = submit_answer(graph, "pii2", "single", turn["question_id"])
    turn = submit_answer(graph, "pii2", "123-45-6789", turn["question_id"])
    assert turn["field"] == "taxpayer_ssn" and "can't accept" in turn["notice"]
    assert not _file_contains(db, "123-45-6789")
