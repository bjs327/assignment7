"""The Streamlit dashboard, driven headlessly with Streamlit's AppTest: a taxpayer runs a whole
session, and a preparer resolves Evaluation Case 2 from the review tab. Offline W-2 reader, real
tax_math tools, a throwaway SQLite file. No API key or browser needed."""

import json

import pytest
from streamlit.testing.v1 import AppTest

from ..orchestrator.scenarios import ROOT

APP = str(ROOT / "src" / "app" / "dashboard.py")
DEMO = ROOT / "demo"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SILVERFILE_DB", str(tmp_path / "sessions.sqlite"))
    monkeypatch.setenv("SILVERFILE_OFFLINE", "1")
    monkeypatch.setattr("app.documents.UPLOAD_ROOT", tmp_path / "uploads")

    def make():
        return AppTest.from_file(APP, default_timeout=120).run()

    return make


def _no_errors(at):
    assert not at.exception, [e.value for e in at.exception]


def _question(at):
    qs = [m.value for m in at.markdown if m.value.startswith("### ")]
    return qs[0] if qs else None


def _answer(at, text):
    [box] = [t for t in at.text_input if t.key and t.key.startswith("a-")]
    box.input(text)
    [b for b in at.button if b.label == "Send"][0].click()
    return at.run()


def _taxpayer_session(at, sid, answers_file):
    at.text_input[0].set_value(sid).run()
    at.button(key=f"demo-{sid}-1").click().run()
    forms = json.loads((DEMO / "eval_case_1_forms.json").read_text())["documents"]
    for form in forms:
        at.selectbox(key=f"type-{sid}").set_value(form["document_type"]).run()
        if form["recipient"] == "spouse":
            [r for r in at.radio if r.label == "Whose form is it?"][0].set_value("spouse")
        for label_box, value in form["boxes"].items():
            from app.documents import ENTERED_FORMS

            kind, label = ENTERED_FORMS[form["document_type"]][label_box]
            widget = [w for w in (at.checkbox if kind == "flag" else at.text_input) if w.label == label][0]
            widget.check() if kind == "flag" else widget.input(value)
        [b for b in at.button if b.label == "Add this form"][0].click()
        at.run()
        _no_errors(at)
    at.button(key=f"start-{sid}").click().run()
    for text in (DEMO / answers_file).read_text().splitlines():
        assert _question(at), "the interview ended before the answers ran out"
        _answer(at, text)
        _no_errors(at)
    return at


def test_a_taxpayer_runs_eval_case_1_in_the_dashboard(app):
    at = app()
    _no_errors(at)
    at = _taxpayer_session(at, "dash1", "eval_case_1_answers.txt")
    assert [s.value for s in at.success] == ["Thank you. Your draft return is ready for a Kushim preparer to check."]
    assert [b.label for b in at.get("download_button")] == ["Download the draft Form 1040-SR (HTML)"]

    # The preparer sees the finished session with the full audit record.
    at.sidebar.radio[0].set_value("Kushim preparer").run()
    _no_errors(at)
    assert "intake → extraction → eligibility_rules" in " ".join(m.value for m in at.markdown)


def test_coming_back_to_a_session_shows_what_was_covered(app):
    at = app()
    at.text_input[0].set_value("dash2").run()
    at.button(key="demo-dash2-1").click().run()
    at.button(key="start-dash2").click().run()
    _answer(at, "married filing jointly")

    later = app()  # a new browser session
    later.text_input[0].set_value("dash2").run()
    welcome = [i.value for i in later.info if i.value.startswith("Welcome back")]
    assert welcome and "Married filing jointly" in welcome[0]
    assert "Social Security number" in _question(later)


def test_a_preparer_resolves_eval_case_2_in_the_dashboard(app):
    at = _taxpayer_session(app(), "dash3", "eval_case_2_answers.txt")
    assert any("Kushim preparer" in i.value for i in at.info)  # the taxpayer is told, not shown the issue

    at.sidebar.radio[0].set_value("Kushim preparer").run()
    assert any("Filing status unclear" in m.value for m in at.markdown)
    # Without a name nothing is recorded, and the problem is shown.
    [b for b in at.button if b.label == "Record decisions and continue"][0].click()
    at.run()
    assert any("give your name" in e.value for e in at.error)

    [name] = [t for t in at.text_input if t.label.startswith("Your name")]
    name.input("Preparer Pat")
    [radio] = [r for r in at.radio if r.key and r.key.startswith("d-")]
    radio.set_value("choose_filing_status")
    [fs] = [s for s in at.selectbox if s.key and s.key.startswith("fs-")]
    fs.set_value("married_filing_jointly")
    [b for b in at.button if b.label == "Record decisions and continue"][0].click()
    at.run()
    _no_errors(at)
    assert any("Nothing to decide" in m.value and "approved" in m.value for m in at.markdown)
