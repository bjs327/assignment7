"""Computation agent: real Eligibility in front, real tax_math over MCP, real Review behind."""

import json
from pathlib import Path

import pytest

from agents.computation import ToolCallError, make_computation_node, mcp_tool_caller, run_computation
from agents.eligibility.agent import eligibility_node
from agents.extraction.agent import aggregate_extractions, merge_extractions
from agents.review.node import run_review

from .scenarios import MFJ_EXPECTED, mfj_return

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures"


@pytest.fixture(scope="module")
def call():
    return mcp_tool_caller()


def prepared(state: dict) -> dict:
    """Run the real Eligibility agent first, as the Orchestrator will."""
    state.update(eligibility_node(state))
    assert state["eligibility"]["status"] == "complete", state["eligibility"]
    return state


def computed(state: dict, call) -> dict:
    state.update(make_computation_node(call)(state))
    return state


def values(state: dict) -> dict[str, int]:
    return {line: rec["value"] for line, rec in state["draft_return"]["lines"].items()}


def with_charitable_ira(state: dict, **boxes: str) -> dict:
    """Code Y needs an IRA owner aged 70 1/2 or older, so make the taxpayer older."""
    state["intake_record"]["taxpayer"]["date_of_birth"] = "1950-05-10"
    state["entered_documents"][3]["boxes"].update({"7": "7Y", **boxes})
    return state


def entry(amount: str) -> dict:
    return {"amount": amount, "entered_by": "taxpayer", "entered_at": "2026-10-25T14:00:00+00:00"}


def test_mfj_return_matches_hand_worked_values_and_review_approves(call):
    state = computed(prepared(mfj_return()), call)
    assert state["computation_status"] == "complete"
    assert {line: values(state)[line] for line in MFJ_EXPECTED} == MFJ_EXPECTED

    report, escalations = run_review(state, call)
    assert report.verdict == "approved", [f.message for f in report.findings]
    assert report.findings == [] and escalations == []


def test_every_line_records_its_tool_working_and_rules(call):
    lines = computed(prepared(mfj_return()), call)["draft_return"]["lines"]
    for line, rec in lines.items():
        assert rec["source"] == "tool" and rec["tool"] and isinstance(rec["inputs"], dict), line
        assert int(rec["steps"][-1]["amount"]) == rec["value"], line
        assert rec["citations"], line
    assert lines["1z"]["documents"] == ["w2-1"]
    assert lines["6a"]["entered_documents"] == ["ssa-1", "ssa-2"]
    assert lines["1z"]["inputs"]["items"][0]["label"] == "W-2 #1 (Maple Grove Public Library) box 1"
    # 6b keeps the other-income total it was given, so a reader can see where that came from.
    assert lines["6b"]["supporting"][0]["value"] == 59700


def test_draft_return_is_a_draft_and_carries_no_ssn(call):
    draft = computed(prepared(mfj_return()), call)["draft_return"]
    assert draft["status"] == "draft"
    assert "900-12-3456" not in json.dumps(draft) and "901-22-3333" not in json.dumps(draft)


def test_qualified_charitable_distribution_is_left_out_of_line_4b(call):
    state = computed(prepared(with_charitable_ira(mfj_return(), qcd_amount="5000.00")), call)
    lines = state["draft_return"]["lines"]
    assert (lines["4a"]["value"], lines["4b"]["value"]) == (40000, 35000)
    assert lines["4b"]["tool"] == "subtract_amounts"
    assert run_review(state, call)[0].verdict == "approved"


def test_charity_amount_is_asked_for_not_assumed(call):
    state = computed(prepared(with_charitable_ira(mfj_return())), call)
    assert state["computation_status"] == "needs_input"
    assert state["computation_missing"] == [{
        "field": "document.r-1.qcd_amount",
        "reason": "r-1 is a qualified charitable distribution: how much of it went directly to charity?",
        "route_to": "intake",
    }]
    assert "draft_return" not in state


def test_entered_amounts_and_the_student_loan_interest_carve_out(call):
    state = mfj_return()
    state["entered_amounts"] = {"10": entry("2500.00"), "student_loan_interest": entry("1000.00")}
    state = computed(prepared(state), call)
    lines = state["draft_return"]["lines"]
    assert lines["10"]["value"] == 2500 and lines["10"]["entered_by"] == "taxpayer"
    assert lines["11a"]["value"] == 93700 - 2500
    # The worksheet counts every adjustment except student loan interest: 2,500 - 1,000.
    assert lines["6b"]["inputs"]["adjustments"] == "1500"
    assert run_review(state, call)[0].verdict == "approved"


def test_blank_required_box_goes_back_to_intake(call):
    state = mfj_return()
    del state["entered_documents"][0]["boxes"]["5"]
    state.update(eligibility_node(state))
    state = computed(state, call)
    assert state["computation_status"] == "needs_input"
    assert {"field": "document.ssa-1.5", "reason": "ssa-1 box 5 is blank or unreadable.",
            "route_to": "intake"} in state["computation_missing"]


def test_waits_for_eligibility(call):
    state = computed(mfj_return(), call)  # Eligibility hasn't run
    assert state["computation_status"] == "blocked"
    assert state["computation_missing"][0]["route_to"] == "eligibility"


def test_married_filing_separately_needs_the_line_6d_answer():
    state = mfj_return()
    state["eligibility"] = {"status": "complete", "documents": [],
                            "computation_inputs": {"filing_status": "married_filing_separately",
                                                   "lived_apart_from_spouse_all_year": None}}
    state["extracted_documents"], state["entered_documents"] = [], []
    update = run_computation(state, call=lambda tool, args: pytest.fail("no tool should run"))
    assert update["computation_status"] == "needs_input"
    assert update["computation_missing"][0]["field"] == "lived_apart_from_spouse_all_year"


def test_a_refused_tool_call_is_reported_not_swallowed():
    state = prepared(mfj_return())

    def refuse(tool, args):
        raise ToolCallError(tool, "filing_status: 'bogus' is not a filing status")

    update = run_computation(state, refuse)
    assert update["computation_status"] == "error"
    assert update["computation_missing"][0]["route_to"] == "human"


def test_a_send_back_recomputes_the_same_return(call):
    state = computed(prepared(mfj_return()), call)
    first = values(state)
    state["revision_requests"] = {"computation": [{"finding_id": "R03-replay:16"}]}
    state = computed(state, call)
    assert values(state) == first
    assert state["computation_revision"] == 2
    assert state["trace"][-1]["answered_findings"] == ["R03-replay:16"]


def test_reads_the_older_w2_field_names_in_davids_fixture(call):
    state = json.loads((FIXTURES / "eligibility_clean_mfj.json").read_text())
    state["entered_documents"][0]["boxes"]["qcd_amount"] = "12000.00"
    state = computed(prepared(state), call)
    lines = state["draft_return"]["lines"]
    assert (lines["1z"]["value"], lines["25a"]["value"]) == (18500, 1200)
    assert (lines["4a"]["value"], lines["4b"]["value"]) == (12000, 0)


def with_extraction_placeholders(state: dict) -> dict:
    """Run Extraction's real aggregation step, which adds a placeholder for every form it doesn't read."""
    state["documents"] = [{"document_id": d["document_id"], "document_type": d["document_type"]}
                          for d in state["extracted_documents"] + state["entered_documents"]]
    update = aggregate_extractions(state)
    state["extracted_documents"] = merge_extractions(state["extracted_documents"], update["extracted_documents"])
    return state


def test_extraction_placeholders_for_typed_in_forms_are_skipped(call):
    state = with_extraction_placeholders(mfj_return())
    assert {d["status"] for d in state["extracted_documents"]} == {"extracted", "manual_entry_required"}
    state = computed(prepared(state), call)
    assert state["computation_status"] == "complete", state["computation_missing"]
    assert {line: values(state)[line] for line in MFJ_EXPECTED} == MFJ_EXPECTED
    assert run_review(state, call)[0].verdict == "approved"


def test_a_placeholder_with_nothing_typed_in_goes_to_intake(call):
    state = with_extraction_placeholders(mfj_return())
    state["entered_documents"] = [d for d in state["entered_documents"] if d["document_id"] != "ssa-2"]
    state.update(eligibility_node(state))
    state = computed(state, call)
    assert state["computation_status"] == "needs_input"
    assert state["computation_missing"] == [{"field": "document.ssa-2",
                                             "reason": "The amounts on ssa-2 haven't been entered yet.",
                                             "route_to": "intake"}]


def test_a_w2_typed_in_by_hand_replaces_a_failed_extraction(call):
    state = mfj_return()
    state["extracted_documents"][0].update(status="failed", fields={})
    state["entered_documents"].append({"document_id": "w2-1", "document_type": "W-2", "recipient": "taxpayer",
                                       "tax_year": 2025, "boxes": {"1": "18500.00", "2": "1850.00"}})
    state = computed(prepared(state), call)
    assert state["computation_status"] == "complete", state["computation_missing"]
    lines = state["draft_return"]["lines"]
    assert (lines["1z"]["value"], lines["25a"]["value"]) == (18500, 1850)  # counted once, not twice
    assert lines["1z"]["entered_documents"] == ["w2-1"] and "documents" not in lines["1z"]


def test_document_type_spellings_match_eligibility(call):
    state = mfj_return()
    for doc, spelling in zip(state["entered_documents"], ["SSA1099", "ssa 1099", "1099 INT", "1099R"]):
        doc["document_type"] = spelling
    state = computed(prepared(state), call)
    assert state["computation_status"] == "complete", state["computation_missing"]
    assert {line: values(state)[line] for line in MFJ_EXPECTED} == MFJ_EXPECTED
