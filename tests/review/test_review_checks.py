import copy

import pytest

from agents.review import APPROVED, ESCALATE, REVISE, run_review
from conftest import clean_state, fake_tool_caller


def review(state, caller=fake_tool_caller):
    return run_review(state, caller)[0]


def ids(report):
    return {f.id for f in report.findings if f.severity == "blocker"}


def test_clean_return_is_approved(state):
    r = review(state)
    assert r.verdict == APPROVED, r.findings
    assert not r.findings


def test_review_never_modifies_the_return(state):
    before = copy.deepcopy(state)
    state["draft_return"]["lines"]["9"]["value"] = 1  # make it fail, so every check runs hard
    snapshot = copy.deepcopy(state)
    review(state)
    assert state == snapshot and before != snapshot


# Falsification: change one printed number and Review must notice. Every line
# below has something downstream or an input link that depends on it.
MUTATE = ["1z", "2b", "6a", "6b", "9", "10", "11a", "12e", "13b", "14", "15", "16", "24",
          "25a", "25d", "26", "33", "34", "37"]


@pytest.mark.parametrize("line", MUTATE)
def test_changing_any_one_number_is_caught(state, line):
    state["draft_return"]["lines"][line]["value"] += 1
    r = review(state)
    assert r.verdict != APPROVED, f"mutating line {line} was not caught"


def test_changing_the_tax_exempt_interest_is_caught_via_the_input_link(state):
    state["draft_return"]["lines"]["2a"]["value"] = 50
    assert any(f.check == "R06-stale-input" for f in review(state).findings)


def test_mutated_line_is_sent_back_to_whoever_made_it(state):
    state["draft_return"]["lines"]["16"]["value"] += 10   # tool line
    state["draft_return"]["lines"]["10"]["value"] += 10   # typed-in line
    r = review(state)
    assert r.verdict == REVISE
    assert "computation" in r.revision_requests
    assert any(x["line"] == "16" for x in r.revision_requests["computation"])


def test_line_6b_more_than_6a_is_a_contradiction(state):
    L = state["draft_return"]["lines"]
    L["6a"]["value"] = 1000
    r = review(state)
    assert any(f.check == "R05-contradiction" and f.line == "6b" for f in r.findings)


def test_the_059_rounding_edge_is_not_a_false_alarm(state):
    # tax_math notes: $0.59 of benefits -> 6a prints 1, 6b prints 1. Not a contradiction.
    L = state["draft_return"]["lines"]
    L["6a"]["value"] = 1
    L["6b"]["value"] = 1
    assert not any(f.check == "R05-contradiction" for f in review(state).findings)


def test_stale_input_catches_the_ordering_dependency(state):
    # Senior deduction was asked about with an AGI from before 6b was final.
    L = state["draft_return"]["lines"]
    L["13b"]["inputs"]["modified_agi"] = "50100"
    r = review(state)
    assert any(f.check == "R06-stale-input" and f.line == "13b" for f in r.findings)
    assert r.verdict == REVISE


def test_missing_provenance_is_a_blocker(state):
    del state["draft_return"]["lines"]["16"]["steps"]
    assert "R02-provenance:16" in ids(review(state))


def test_working_that_does_not_end_at_the_number_is_a_blocker(state):
    state["draft_return"]["lines"]["16"]["steps"][-1]["amount"] = "3400"
    assert "R02-provenance:16" in ids(review(state))


def test_typed_in_value_must_say_who_and_when(state):
    del state["draft_return"]["lines"]["10"]["entered_by"]
    r = review(state)
    assert r.verdict == REVISE and "intake" in r.revision_requests


def test_tool_error_is_a_finding_not_a_crash(state):
    def broken(tool, inputs):
        raise RuntimeError("server down")
    r = run_review(state, broken)[0]
    assert r.verdict == REVISE and any(f.check == "R03-replay" for f in r.findings)


def test_no_tool_caller_never_silently_passes_replay(state):
    r = run_review(state, None)[0]
    assert any(f.check == "R03-replay" and f.severity == "warning" for f in r.findings)


def test_a_crashing_check_cannot_look_like_a_pass(state, monkeypatch):
    import agents.review.checks as checks
    def boom(ctx):
        raise KeyError("x")
    monkeypatch.setattr(checks, "ALL_CHECKS", [boom])
    monkeypatch.setattr("agents.review.node.ALL_CHECKS", [boom])
    r = review(state)
    assert r.verdict == ESCALATE


def test_empty_return_is_not_a_pass():
    assert review({"draft_return": {}}).verdict == ESCALATE


# ---- eligibility -------------------------------------------------------

def test_born_too_late_for_1040sr_escalates(state):
    state["draft_return"]["taxpayer"]["dob"] = "1961-01-02"
    state["draft_return"]["boxes_12d"]["taxpayer_65"] = False
    assert review(state).verdict == ESCALATE


def test_born_on_new_years_day_1961_is_eligible(state):
    state["draft_return"]["taxpayer"]["dob"] = "1961-01-01"
    assert review(state).verdict == APPROVED


def test_joint_return_needs_only_one_spouse(state):
    r = state["draft_return"]
    r["filing_status"] = "married_filing_jointly"
    r["taxpayer"]["dob"] = "1970-01-01"
    r["spouse"] = {"dob": "1958-01-01"}
    r["boxes_12d"] = {"taxpayer_65": False, "spouse_65": True}
    r["lines"]["13b"]["value"] = 6000
    r["lines"]["13b"]["steps"][-1]["amount"] = "6000"
    assert not any(f.check == "R07-eligibility" for f in review(state).findings)


def test_age_box_must_match_date_of_birth(state):
    state["draft_return"]["boxes_12d"]["taxpayer_65"] = False
    r = review(state)
    assert any(f.check == "R07-eligibility" and f.line == "12d" for f in r.findings)
    assert "eligibility" in r.revision_requests


def test_senior_deduction_is_capped_per_qualifying_person(state):
    L = state["draft_return"]["lines"]
    L["13b"]["value"] = 12000                       # one qualifier, two people's worth
    assert any(f.check == "R07-eligibility" and f.line == "13b" for f in review(state).findings)


# ---- scope, SSNs, escalations ----------------------------------------------

@pytest.mark.parametrize("doc_type", ["1099-NEC", "K-1", "1099-B"])
def test_out_of_scope_document_escalates(state, doc_type):
    state["documents"].append({"document_id": "x1", "document_type": doc_type})
    assert review(state).verdict == ESCALATE


def test_1099r_taxable_amount_not_determined_escalates(state):
    state["extracted_documents"].append({"document_id": "r1", "status": "extracted",
                                         "document_type": "1099-R", "taxable_amount_not_determined": True})
    assert review(state).verdict == ESCALATE


@pytest.mark.parametrize("flag", ["claimed_as_dependent", "spouse_itemizes", "dual_status_alien", "itemizing"])
def test_chart_does_not_apply_conditions_escalate(state, flag):
    state["draft_return"]["scope_facts"][flag] = True
    assert review(state).verdict == ESCALATE


def test_capital_gain_line_escalates(state):
    state["draft_return"]["lines"]["7"] = {"value": 300, "source": "user_input", "entered_by": "t", "entered_at": "x"}
    assert review(state).verdict == ESCALATE


def test_real_looking_ssn_escalates_and_is_never_repeated(state):
    state["draft_return"]["dependents"] = [{"name": "A", "ssn": "123-45-6789"}]
    r = review(state)
    assert r.verdict == ESCALATE
    assert "123" not in str(r.to_dict()) and "6789" not in str(r.to_dict())


def test_test_range_ssn_is_fine(state):
    state["draft_return"]["dependents"] = [{"name": "A", "ssn": "901-45-6789"}]
    assert review(state).verdict == APPROVED


def test_unresolved_escalation_blocks_approval(state):
    state["escalations"] = [{"source_agent": "intake", "code": "tax_advice_request"}]
    assert review(state).verdict == ESCALATE


def test_resolved_escalation_does_not_block(state):
    state["escalations"] = [{"source_agent": "intake", "code": "tax_advice_request", "resolved": True}]
    assert review(state).verdict == APPROVED


def test_advice_in_notes_escalates(state):
    state["draft_return"]["notes"] = ["You should convert your IRA to a Roth."]
    assert review(state).verdict == ESCALATE


def test_return_that_claims_to_be_filed_escalates(state):
    state["draft_return"]["filed"] = True
    assert review(state).verdict == ESCALATE


# ---- source documents -------------------------------------------------------

def test_line_from_unreadable_w2_goes_back_to_extraction_with_the_document_id(state):
    state["extracted_documents"][0]["status"] = "needs_review"
    r = review(state)
    assert r.verdict == REVISE
    assert r.reextract_document_ids == ["w2-1"]
    assert "extraction" in r.revision_requests
