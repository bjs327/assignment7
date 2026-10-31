"""The orchestrator's decision table: given a state, which agent acts next, and which
conditions interrupt for a human (specification sections 4.1 and 4.4).

What is tested:
- Which agent the Orchestrator sends work to next, for each agent's possible results: the
  normal order, Eligibility & Rules asking Intake for facts, Computation's missing inputs going
  where Computation says, Review sending work back, and finished work marked stale when Intake
  runs again.
- An open escalation stops all work until a person decides, and a "stop" ends the run.
- The loop guard fires on a repeating hand-off, counts again after a person steps in, and isn't
  fooled by an approval that follows two rounds of revision.
- Each of the six escalation conditions in section 4.4 pauses the full graph for a person, with
  the options from the escalation table, before anything is computed.

Owner: Verena.
"""

import pytest

from agents.extraction import FixtureW2Extractor
from agents.intake import make_escalation
from agents.orchestrator import pending_review
from agents.orchestrator.agent import new_ledger, orchestrator_node
from graph import build_graph
from memory import make_checkpointer

from .orchestrator.scenarios import run, session

TASKS = ["intake", "extraction", "eligibility", "computation", "review"]


def _ledger(**statuses) -> dict:
    ledger = new_ledger()
    for task, status in statuses.items():
        ledger[task] = {**ledger[task], "status": status}
    return ledger


def _after(node: str, statuses: dict, **outputs) -> dict:
    """The orchestrator's decision right after ``node`` ran and wrote ``outputs``."""
    return orchestrator_node({"task_ledger": _ledger(**statuses), "route_history": [node], **outputs})


def _done(*tasks: str, **others) -> dict:
    return {**dict.fromkeys(tasks, "done"), **others}


# --- Which agent acts next --------------------------------------------------------------------


def test_a_new_session_starts_with_intake():
    update = orchestrator_node({})
    assert update["next_step"] == "intake"
    assert update["task_ledger"]["intake"]["status"] == "running"
    assert update["task_ledger"]["intake"]["attempts"] == 1


@pytest.mark.parametrize("node, statuses, outputs, next_step", [
    ("intake", {"intake": "running"}, {"intake_status": "complete"}, "extraction"),
    ("extraction", _done("intake", extraction="running"), {}, "eligibility_rules"),
    ("eligibility_rules", _done("intake", "extraction", eligibility="running"),
     {"eligibility": {"status": "complete"}}, "computation"),
    ("computation", _done("intake", "extraction", "eligibility", computation="running"),
     {"computation_status": "complete"}, "review"),
    ("review", _done("intake", "extraction", "eligibility", "computation", review="running"),
     {"review_report": {"verdict": "approved"}}, "finalize"),
])
def test_each_task_runs_once_the_tasks_it_reads_are_done(node, statuses, outputs, next_step):
    assert _after(node, statuses, **outputs)["next_step"] == next_step


def test_eligibility_questions_go_to_intake_with_eligibilitys_note():
    update = _after("eligibility_rules", _done("intake", "extraction", eligibility="running"),
                    eligibility={"status": "needs_facts"}, eligibility_reask=["taxpayer_dob"],
                    eligibility_notice="I need one more answer.")
    assert update["next_step"] == "intake"
    assert update["intake_reask"] == ["taxpayer_dob"] and update["intake_notice"] == "I need one more answer."


def test_eligibility_waiting_for_computation_is_blocked_then_rerun():
    update = _after("computation", _done("intake", "extraction", "eligibility", computation="running"),
                    computation_status="blocked", computation_missing=[])
    assert update["next_step"] == "eligibility_rules"


@pytest.mark.parametrize("missing, next_step, written", [
    ({"field": "document.int-1", "reason": "Not entered yet.", "route_to": "intake"},
     "intake", {"intake_reask": ["document.int-1"]}),
    ({"field": "document.w2-1.1", "reason": "Box 1 unreadable.", "route_to": "extraction"},
     "extraction", {"reextract_document_ids": ["w2-1"]}),
    ({"field": "document.r-1", "reason": "Not cleared.", "route_to": "eligibility"},
     "eligibility_rules", {}),
])
def test_missing_inputs_go_where_computation_says(missing, next_step, written):
    update = _after("computation", _done("intake", "extraction", "eligibility", computation="running"),
                    computation_status="needs_input", computation_missing=[missing])
    assert update["next_step"] == next_step
    assert {k: update[k] for k in written} == written


def test_a_computation_error_goes_to_a_person():
    update = _after("computation", _done("intake", "extraction", "eligibility", computation="running"),
                    computation_status="error",
                    computation_missing=[{"field": "16", "reason": "Tool refused.", "route_to": "human"}])
    assert update["next_step"] == "human_review"
    assert [e["code"] for e in update["escalations"]] == ["computation_error"]


def _sent_back(requests: dict) -> dict:
    """Review's report and state keys for a send-back, as run_review writes them."""
    return {"review_report": {"verdict": "revise", "iteration": 1, "revision_requests": requests},
            "revision_requests": requests}


def test_a_review_send_back_reruns_the_agent_review_names():
    update = _after("review", _done("intake", "extraction", "eligibility", "computation", review="running"),
                    **_sent_back({"computation": [{"finding_id": "R03-replay:9"}]}))
    assert update["next_step"] == "computation"
    assert update["task_ledger"]["review"]["status"] == "pending"


def test_a_review_send_back_to_intake_goes_to_a_person_who_picks_the_questions():
    update = _after("review", _done("intake", "extraction", "eligibility", "computation", review="running"),
                    **_sent_back({"intake": [{"message": "The taxpayer's date of birth is missing."}]}))
    assert update["next_step"] == "human_review"
    assert [e["code"] for e in update["escalations"]] == ["review_intake_request"]


def test_intake_running_again_marks_finished_work_stale():
    update = _after("intake", _done("extraction", "eligibility", "computation", "review", intake="running"),
                    intake_status="complete")
    statuses = {t: update["task_ledger"][t]["status"] for t in TASKS[1:]}
    assert statuses == {"extraction": "running", "eligibility": "stale", "computation": "stale", "review": "stale"}
    assert update["next_step"] == "extraction"  # the first stale task runs again straight away
    assert update["intake_reask"] == [] and update["intake_notice"] == ""


def test_an_open_escalation_stops_all_work_until_a_person_decides():
    open_one = {"source_agent": "document_extraction", "document_id": "w2-1", "code": "unreadable_box",
                "reason": "...", "attempt": 1}
    update = _after("extraction", _done("intake", extraction="running"), escalations=[open_one])
    assert update["next_step"] == "human_review" and update["run_status"] == "awaiting_human"
    assert update["task_ledger"]["extraction"]["status"] == "escalated"

    decided = _after("extraction", _done("intake", extraction="running"),
                     escalations=[{**open_one, "resolved": True}])
    assert decided["next_step"] == "eligibility_rules"


def test_a_stop_ends_the_run():
    update = orchestrator_node({"task_ledger": _ledger(**_done("intake")), "route_history": ["human_review"],
                                "human_decisions": [{"escalation_id": "x", "decision": "stop"}]})
    assert update["next_step"] == "finalize" and update["run_status"] == "handed_to_preparer"
    assert update["task_ledger"]["computation"]["status"] == "stopped"
    assert update["task_ledger"]["intake"]["status"] == "done"


# --- Loop guard -------------------------------------------------------------------------------


def test_loop_guard_breaks_a_repeating_handoff():
    update = orchestrator_node({"task_ledger": _ledger(**_done("intake", extraction="running")),
                                "route_history": ["intake", "extraction"] * 3})
    assert update["next_step"] == "human_review"
    assert [e["code"] for e in update["escalations"]] == ["handoff_cycle"]


def test_loop_guard_only_counts_handoffs_since_a_person_stepped_in():
    update = orchestrator_node({"task_ledger": _ledger(**_done("intake", extraction="running")),
                                "route_history": ["intake", "extraction"] * 3 + ["human_review", "intake", "extraction"]})
    assert update["next_step"] == "eligibility_rules"


def test_an_approval_after_two_revisions_is_not_mistaken_for_a_cycle():
    update = orchestrator_node({"task_ledger": _ledger(**_done(*TASKS[:4], review="running")),
                                "route_history": ["computation", "review"] * 3,
                                "review_report": {"verdict": "approved"}})
    assert update["next_step"] == "finalize" and update["run_status"] == "approved"


# --- Every escalation condition in section 4.4 pauses for a person ----------------------------


def _out_of_scope(state):
    state["intake_answers"]["documents"].append({"type": "1099-NEC", "count": 1})


def _user_document_conflict(state):
    state["intake_answers"]["documents"][0]["count"] = 3  # said three W-2s, supplied two


def _taxable_amount_not_determined(state):
    state["entered_documents"][3]["boxes"]["2b_taxable_not_determined"] = True


def _tax_advice(state):
    state["intake_escalations"] = [make_escalation(
        "tax_advice_request", "taxpayer asked for tax advice", "filing_status",
        "Would we pay less if we filed separately?")]


def _spouse_died(state):
    state["eligibility_facts"]["spouse_died_in_2025"] = True
    state["eligibility_facts"]["spouse_date_of_death"] = "2025-09-30"  # after turning 65 on 2025-07-18 (ELIG-02)


def _exploitation(state):
    state["intake_escalations"] = [make_escalation(
        "financial_exploitation", "possible financial exploitation", "has_estimated_payments",
        "My nephew tells me to send the refund to his account")]


@pytest.mark.parametrize("make_condition, raised_by, title, options", [
    (_out_of_scope, "eligibility_rules", "Out of scope", ["re_ask", "stop"]),
    (_user_document_conflict, "eligibility_rules", "An answer doesn't match a document",
     ["accept_documents", "re_ask", "stop"]),
    (_taxable_amount_not_determined, "eligibility_rules", "1099-R taxable amount not determined", ["stop"]),
    (_tax_advice, "intake", "Tax advice requested", ["noted", "stop"]),
    (_spouse_died, "eligibility_rules", "Filing status unclear", ["choose_filing_status", "re_ask", "stop"]),
    (_exploitation, "intake", "Possible financial exploitation or third-party pressure", ["checked_safe", "stop"]),
])
def test_each_escalation_condition_interrupts_for_a_human(make_condition, raised_by, title, options):
    graph = build_graph(make_checkpointer(), extractor=FixtureW2Extractor(),
                        computation_caller=lambda *a: pytest.fail("ran past the escalation"),
                        review_caller=lambda *a: pytest.fail("ran past the escalation"))
    state = session()
    make_condition(state)
    snap = run(graph, state)
    assert snap.values["run_status"] == "awaiting_human"
    packet = pending_review(graph, "session_1")
    issue = next(i for i in packet["issues"] if i["title"] == title)
    assert issue["source_agent"] == raised_by
    assert [o["decision"] for o in issue["options"]] == options
    assert "computation" not in snap.values["route_history"]  # nothing was computed past it
