"""SilverFile dashboard (Streamlit). Start it with ``python run.py dashboard`` (add ``--offline``
to read W-2s with the offline test reader instead of the vision model).

Two views:

- **Taxpayer.** Upload W-2 images, type in the other forms, then the interview, one question at
  a time. Coming back to a session shows what was already covered.
- **Kushim preparer** (the "superuser" view). Every saved session and its status; the review
  packet of a paused session, with the allowed decisions for each issue; the draft Form 1040-SR
  with each amount's provenance one click away; and the audit record (task ledger, route,
  escalations, decisions).

Everything goes through ``app.session`` and the Orchestrator's ``submit_decisions``, the same
calls the terminal programs use. The dashboard keeps nothing of its own: a session's state is
always read back from its checkpoint.

Known limitation: there is no login. Anyone who can open the dashboard can act as a preparer.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402

from agents.orchestrator import submit_decisions  # noqa: E402
from agents.orchestrator.human_review import DECISIONS  # noqa: E402
from app import session as sessions  # noqa: E402
from app.documents import (  # noqa: E402
    ENTERED_FORMS,
    RegistrationError,
    enter_amount,
    enter_document,
    register_path,
    register_upload,
)
from app.form1040sr import build_form, render_html  # noqa: E402
from agents.computation.inputs import ENTERED_LINES  # noqa: E402
from memory import list_sessions  # noqa: E402

REPO_ROOT = SRC.parent
DEMO_W2S = [REPO_ROOT / "demo" / "w2_image_1.jpg", REPO_ROOT / "demo" / "w2_image_2.jpg"]


def _args() -> argparse.Namespace:
    """``--db`` and ``--offline`` from run.py; SILVERFILE_DB and SILVERFILE_OFFLINE=1 also work
    (used by the headless dashboard tests)."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.environ.get("SILVERFILE_DB") or None)
    parser.add_argument("--offline", action="store_true", default=os.environ.get("SILVERFILE_OFFLINE") == "1")
    return parser.parse_known_args(sys.argv[1:])[0]


ARGS = _args()


@st.cache_resource
def _graph(db: str | None, offline: bool):
    return sessions.open_graph(db, offline=offline)


GRAPH = _graph(ARGS.db, ARGS.offline)

st.set_page_config(page_title="SilverFile", layout="wide")
# Larger type throughout: many of the people using this read small print with difficulty.
st.markdown("<style>html, body, [class*='css'] {font-size: 20px;} .stButton button {font-size: 20px;}</style>",
            unsafe_allow_html=True)


def _show_form(source: dict, key: str) -> None:
    form = build_form(source)
    page = render_html(form)
    st.download_button("Download the draft Form 1040-SR (HTML)", page, file_name=f"{form['session_id']}_1040sr.html",
                       mime="text/html", key=f"dl-{key}")
    components.html(page, height=1400, scrolling=True)


# --- Taxpayer view ---------------------------------------------------------------------------


def _pending(session_id: str) -> dict:
    return st.session_state.setdefault(f"pending-{session_id}",
                                       {"documents": [], "entered": [], "amounts": {}, "hashes": set()})


def _setup(session_id: str) -> None:
    pending = _pending(session_id)
    st.subheader("Step 1: Your W-2 forms")
    st.write("Upload a clear photo or scan of each W-2 (JPG, PNG or WEBP). If you have no W-2, skip this step.")
    files = st.file_uploader("W-2 images", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True,
                             key=f"up-{session_id}")
    for f in files or []:
        data = f.getvalue()
        if f.file_id in pending["hashes"]:
            continue
        try:
            pending["documents"].append(register_upload(session_id, f.name, data, pending["documents"]))
        except RegistrationError as err:
            st.warning(f"{f.name}: {err}")
        pending["hashes"].add(f.file_id)
    if ARGS.offline:
        st.caption("Offline mode: uploaded images can't be read without the vision model. "
                   "Use the demo W-2s instead.")
        for n, path in enumerate(DEMO_W2S, start=1):
            if st.button(f"Add demo W-2 {n} ({path.name})", key=f"demo-{session_id}-{n}"):
                try:
                    pending["documents"].append(register_path(session_id, path, pending["documents"]))
                except RegistrationError as err:
                    st.warning(str(err))
    for d in pending["documents"]:
        st.write(f"Received: W-2 **{d['document_id']}**")

    st.subheader("Step 2: Your other tax forms")
    st.write("Type in the boxes from each SSA-1099, 1099-INT, 1099-DIV or 1099-R. Leave a box empty if it is blank.")
    doc_type = st.selectbox("Which form?", list(ENTERED_FORMS), key=f"type-{session_id}")
    with st.form(f"enter-{session_id}-{doc_type}", clear_on_submit=True):
        recipient = st.radio("Whose form is it?", ["taxpayer", "spouse"], horizontal=True,
                             format_func=lambda r: "Mine" if r == "taxpayer" else "My spouse's")
        boxes = {}
        for box, (kind, label) in ENTERED_FORMS[doc_type].items():
            boxes[box] = st.checkbox(label) if kind == "flag" else st.text_input(label)
        who = st.text_input("Your name or role (recorded as who entered this)", value="taxpayer")
        if st.form_submit_button("Add this form"):
            try:
                doc = enter_document(doc_type, recipient, boxes, who, pending["entered"])
                pending["entered"].append(doc)
                st.success(f"Added {doc_type} {doc['document_id']}.")
            except RegistrationError as err:
                st.error(str(err))
    for d in pending["entered"]:
        st.write(f"Added: {d['document_type']} **{d['document_id']}** ({d['recipient']}): {d['boxes']}")

    with st.expander("Optional: amounts from Schedules 1 to 3, entered directly"):
        with st.form(f"amount-{session_id}", clear_on_submit=True):
            line = st.selectbox("Line", list(ENTERED_LINES), format_func=lambda k: ENTERED_LINES[k])
            amount = st.text_input("Amount")
            who = st.text_input("Entered by", value="taxpayer")
            if st.form_submit_button("Add this amount"):
                try:
                    key, entry = enter_amount(line, amount, who)
                    pending["amounts"][key] = entry
                except RegistrationError as err:
                    st.error(str(err))
        for key, entry in pending["amounts"].items():
            st.write(f"{ENTERED_LINES[key]}: ${entry['amount']}")

    st.subheader("Step 3: A few questions")
    st.write("I'll ask one question at a time. You can stop at any point and come back later.")
    if st.button("Start my questions", type="primary", key=f"start-{session_id}"):
        sessions.start(GRAPH, session_id, documents=pending["documents"], entered_documents=pending["entered"],
                       entered_amounts=pending["amounts"])
        st.session_state[f"seen-{session_id}"] = True
        st.rerun()


def taxpayer_view() -> None:
    st.title("SilverFile")
    st.write("SilverFile helps prepare a draft of your 2025 Form 1040-SR for a Kushim preparer to check. "
             "It never files anything and doesn't give tax advice.")
    default = st.session_state.setdefault("new_session_id", f"sf-{secrets.token_hex(3)}")
    session_id = st.text_input("Your session code (write it down to come back later)", value=default).strip()
    if not session_id:
        return
    try:
        current = sessions.status(GRAPH, session_id)
    except ValueError as err:
        st.error(str(err))
        return

    if current["state"] == "not_started":
        _setup(session_id)
        return
    if current["state"] == "question":
        q = current["question"]
        if not st.session_state.get(f"seen-{session_id}"):
            st.info(sessions.welcome_back(current))
            st.session_state[f"seen-{session_id}"] = True
        p = q["progress"]
        st.progress(min(p["answered"] / max(p["total"], 1), 1.0), text=f"{p['answered']} of {p['total']} answered")
        if q.get("notice"):
            st.info(q["notice"])
        st.markdown(f"### {q['question']}".replace("\n", "  \n"))
        if q.get("current_answer"):
            st.caption(f"Your answer so far: {q['current_answer']}")
        with st.form(f"q-{q['question_id']}", clear_on_submit=True):
            text = st.text_input("Your answer", key=f"a-{q['question_id']}")
            st.caption("You can also say 'go back', 'change my date of birth', 'I'm not sure', or ask for a person.")
            if st.form_submit_button("Send", type="primary"):
                sessions.answer(GRAPH, session_id, q["question_id"], text)
                st.rerun()
        with st.expander("What we've covered so far"):
            from agents.intake import summarize_answers

            st.text(summarize_answers(current.get("intake_answers") or {}))
        return
    if current["state"] == "preparer":
        st.info(current.get("message") or "Thank you. A Kushim preparer is looking at your return and will be "
                "in touch. Your answers are saved.")
        return
    if current["state"] == "finished":
        if current["run_status"] == "approved":
            st.success("Thank you. Your draft return is ready for a Kushim preparer to check.")
            _show_form(current["package"], f"tp-{session_id}")
        else:
            st.info("A Kushim preparer is taking over your return from here. Thank you for your patience.")
        return
    st.warning(f"This session is in an unexpected state ({current['state']}). Please contact Kushim.")


# --- Preparer view ---------------------------------------------------------------------------


def _decide(session_id: str, packet: dict) -> None:
    st.subheader("Decisions needed")
    if packet.get("problems"):
        st.error("Nothing was recorded. Please fix:\n" + "\n".join(f"- {p}" for p in packet["problems"]))
    questions = {q["field"]: q["question"] for q in packet.get("intake_questions") or []}
    statuses = {s["value"]: s["label"] for s in packet.get("filing_statuses") or []}
    with st.form(f"decide-{session_id}"):
        name = st.text_input("Your name (recorded with every decision)")
        decisions = []
        for n, issue in enumerate(packet["issues"], start=1):
            st.markdown(f"**Issue {n}: {issue['title']}** (raised by {issue['raised_by']})")
            st.write(issue["reason"])
            if issue.get("taxpayer_said"):
                st.write(f'The taxpayer said: "{issue["taxpayer_said"]}"')
            if issue.get("document_id"):
                st.write(f"Document: {issue['document_id']}")
            if issue.get("rules"):
                st.caption("Rules: " + "; ".join(issue["rules"]))
            eid = issue["escalation_id"]
            choice = st.radio("Decision", [o["decision"] for o in issue["options"]], key=f"d-{eid}",
                              format_func=lambda d: DECISIONS[d])
            d = {"escalation_id": eid, "decision": choice}
            d["filing_status"] = st.selectbox("Filing status (only for 'Choose the filing status')",
                                              list(statuses), format_func=statuses.get, key=f"fs-{eid}")
            d["reask_fields"] = st.multiselect("Questions to ask again (only for 'Re-ask')", list(questions),
                                               format_func=lambda f: questions[f], key=f"ra-{eid}")
            d["note"] = st.text_input("Note (required for 'Checked: safe to continue'; shown to the taxpayer "
                                      "with 'Re-ask')", key=f"n-{eid}")
            decisions.append(d)
            st.divider()
        if st.form_submit_button("Record decisions and continue", type="primary"):
            for d in decisions:  # send only what the chosen decision uses
                if d["decision"] != "choose_filing_status":
                    d.pop("filing_status")
                if d["decision"] != "re_ask":
                    d.pop("reask_fields")
            submit_decisions(GRAPH, session_id, {"decided_by": name, "decisions": decisions})
            st.rerun()  # a refused answer comes back as the same packet, with its problems listed


def preparer_view() -> None:
    st.title("SilverFile: Kushim preparer dashboard")
    ids = list_sessions(GRAPH.checkpointer)
    if not ids:
        st.write("No sessions yet.")
        return
    rows = []
    for sid in ids:
        s = sessions.status(GRAPH, sid)
        rows.append({"session": sid, "state": s["state"], "run status": s["values"].get("run_status"),
                     "open issues": len(s["packet"]["issues"]) if s["state"] == "preparer" else 0})
    st.dataframe(rows, hide_index=True, width="stretch")
    waiting = [r["session"] for r in rows if r["state"] == "preparer"]
    session_id = st.selectbox("Session", ids, index=ids.index(waiting[0]) if waiting else 0)
    current = sessions.status(GRAPH, session_id)
    values = current["values"]

    tabs = st.tabs(["Review", "Draft Form 1040-SR", "Audit record", "Taxpayer's answers"])
    with tabs[0]:
        if current["state"] == "preparer":
            _decide(session_id, current["packet"])
        else:
            st.write(f"Nothing to decide. This session is: **{current['state']}** "
                     f"({values.get('run_status') or 'not started'}).")
    with tabs[1]:
        source = values.get("final_package") or values
        if source.get("draft_return"):
            if not values.get("final_package"):
                st.warning("Work in progress: this draft hasn't been through the whole run yet.")
            _show_form(source, f"pr-{session_id}")
        else:
            st.write("No draft yet.")
    with tabs[2]:
        ledger = values.get("task_ledger") or {}
        st.markdown("**Task ledger**")
        st.dataframe([{"task": t, **{k: r[k] for k in ("status", "attempts", "note", "updated_at")}}
                      for t, r in ledger.items()], hide_index=True, width="stretch")
        st.markdown("**Route taken**")
        st.write(" → ".join(values.get("route_history") or []))
        st.markdown("**Escalations**")
        st.json(values.get("escalations") or [], expanded=False)
        st.markdown("**Intake's flags**")
        st.json(values.get("intake_escalations") or [], expanded=False)
        st.markdown("**Human decisions**")
        st.json(values.get("human_decisions") or [], expanded=False)
        st.markdown("**Eligibility findings, with citations**")
        st.json((values.get("eligibility") or {}).get("findings") or [], expanded=False)
        st.markdown("**Review report**")
        st.json(values.get("review_report") or {}, expanded=False)
    with tabs[3]:
        from memory import redact_ssns

        st.json(redact_ssns(values.get("intake_record") or current.get("intake_answers") or {}), expanded=True)


view = st.sidebar.radio("View", ["Taxpayer", "Kushim preparer"])
st.sidebar.caption("Offline W-2 reader" if ARGS.offline else "W-2s read by the vision model (needs an API key)")
taxpayer_view() if view == "Taxpayer" else preparer_view()
