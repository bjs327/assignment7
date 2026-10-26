"""The Eligibility & Rules decision table: one test per rule outcome, plus the boundaries."""

from datetime import date

import pytest

from agents.eligibility import evaluate, EligibilityFacts
from agents.eligibility.rules_engine import parse_distribution_codes, reached_70_and_a_half_by

TP_SSN, SP_SSN = "900-12-3456", "901-22-3333"


def facts(**overrides) -> EligibilityFacts:
    """A clean single filer born in 1950 who answered every question. Override to vary."""
    base = {
        "filing_status": "single",
        "taxpayer": {"ssn": TP_SSN, "date_of_birth": "1950-05-10", "blind": False},
        "spouse_died_in_2025": False,
        "can_be_claimed_as_dependent": False,
        "dual_status_alien": False,
    }
    base.update(overrides)
    return EligibilityFacts.model_validate(base)


def joint(**overrides) -> EligibilityFacts:
    base = {
        "filing_status": "married_filing_jointly",
        "spouse": {"ssn": SP_SSN, "date_of_birth": "1952-02-01", "blind": False},
        "spouse_can_be_claimed_as_dependent": False,
    }
    return facts(**{**base, **overrides})


def codes(decision) -> set[str]:
    return {e.code for e in decision.escalations}


def r1099(**boxes) -> dict:
    return {"document_id": "r-1", "document_type": "1099-R", "recipient": "taxpayer",
            "boxes": {"7": "7", "2b_taxable_not_determined": False, "ira_sep_simple": True, **boxes}}


# --- Clean runs --------------------------------------------------------------------------


def test_clean_single_filer_is_complete_with_inputs_for_computation():
    d = evaluate(facts())
    assert d.status == "complete"
    assert d.computation_inputs == {
        "filing_status": "single",
        "taxpayer_65_or_older": True, "taxpayer_blind": False,
        "spouse_65_or_older": False, "spouse_blind": False,
        "taxpayer_qualifies": True, "spouse_qualifies": False,
        "lived_apart_from_spouse_all_year": None,
    }


def test_computation_inputs_are_accepted_by_tax_math():
    from tax_math import senior_deduction, standard_deduction

    inputs = evaluate(joint()).computation_inputs
    std = standard_deduction(**{k: inputs[k] for k in (
        "filing_status", "taxpayer_65_or_older", "taxpayer_blind", "spouse_65_or_older", "spouse_blind")})
    assert std.value == 31_500 + 2 * 1_600
    senior = senior_deduction(modified_agi=60_000, filing_status=inputs["filing_status"],
                              taxpayer_qualifies=inputs["taxpayer_qualifies"],
                              spouse_qualifies=inputs["spouse_qualifies"])
    assert senior.value == 12_000


def test_every_finding_cites_a_rule_with_its_source():
    d = evaluate(joint())
    assert d.findings
    for finding in d.findings:
        assert finding.rule_ids and len(finding.citations) == len(set(finding.rule_ids))
        assert all(c["source"] for c in finding.citations)


# --- ELIG-01: 1040-SR eligibility -------------------------------------------------------


@pytest.mark.parametrize("dob, eligible", [("1961-01-01", True), ("1961-01-02", False), ("1940-06-30", True)])
def test_1040sr_birth_date_boundary(dob, eligible):
    d = evaluate(facts(taxpayer={"ssn": TP_SSN, "date_of_birth": dob, "blind": False}))
    assert d.form_1040sr_eligible is eligible
    assert ("not_1040sr_eligible" in codes(d)) is (not eligible)


def test_joint_return_only_one_spouse_needs_to_qualify():
    d = evaluate(joint(taxpayer={"ssn": TP_SSN, "date_of_birth": "1970-01-01", "blind": False}))
    assert d.form_1040sr_eligible is True and d.status == "complete"
    assert d.line_12d["taxpayer_65_or_older"] is False and d.line_12d["spouse_65_or_older"] is True


def test_separate_return_spouse_age_does_not_count():
    d = evaluate(facts(
        filing_status="married_filing_separately", lived_apart_from_spouse_all_year=True, spouse_itemizes=False,
        taxpayer={"ssn": TP_SSN, "date_of_birth": "1970-01-01", "blind": False},
        spouse={"ssn": SP_SSN, "date_of_birth": "1950-01-01", "blind": False},
        spouse_had_income=True, spouse_filing_return=True, spouse_can_be_claimed_as_dependent=False,
    ))
    assert d.form_1040sr_eligible is False and "not_1040sr_eligible" in codes(d)


# --- ELIG-02: line 12d boxes ------------------------------------------------------------


def test_blind_box_counts():
    d = evaluate(facts(taxpayer={"ssn": TP_SSN, "date_of_birth": "1950-05-10", "blind": True}))
    assert d.line_12d == {"taxpayer_65_or_older": True, "taxpayer_blind": True,
                          "spouse_65_or_older": False, "spouse_blind": False}


def test_head_of_household_never_gets_spouse_boxes():
    d = evaluate(facts(filing_status="head_of_household", paid_over_half_home_cost=True,
                       dependents=[{"name": "Ana", "relationship": "granddaughter", "date_of_birth": "2012-01-01",
                                    "ssn": "902-11-2222"}]))
    assert d.status == "complete"
    assert d.line_12d["spouse_65_or_older"] is False and d.line_12d["spouse_blind"] is False


def _mfs(**overrides):
    base = dict(filing_status="married_filing_separately", lived_apart_from_spouse_all_year=False,
                spouse_itemizes=False, spouse={"ssn": SP_SSN, "date_of_birth": "1950-01-01", "blind": True},
                spouse_can_be_claimed_as_dependent=False)
    base.update(overrides)
    return facts(**base)


def test_separate_return_spouse_boxes_need_no_income_not_filing_not_dependent():
    d = evaluate(_mfs(spouse_had_income=False, spouse_filing_return=False))
    assert d.line_12d["spouse_65_or_older"] is True and d.line_12d["spouse_blind"] is True


def test_separate_return_spouse_boxes_off_when_spouse_has_income():
    d = evaluate(_mfs(spouse_had_income=True, spouse_filing_return=True))
    assert d.line_12d["spouse_65_or_older"] is False and d.line_12d["spouse_blind"] is False


def test_separate_return_spouse_box_conditions_are_asked_not_assumed():
    d = evaluate(_mfs())
    assert d.status == "needs_facts"
    assert {"spouse_had_income", "spouse_filing_return"} <= {m.field for m in d.missing_facts}


# --- ELIG-03: senior deduction ----------------------------------------------------------


def test_separate_filers_get_no_senior_deduction():
    d = evaluate(_mfs(spouse_had_income=True, spouse_filing_return=True))
    assert d.senior_deduction == {"taxpayer_qualifies": False, "spouse_qualifies": False}
    assert next(s for s in d.schedules if s.schedule == "Schedule 1-A").applies is False


def test_senior_deduction_is_per_person_on_a_joint_return():
    d = evaluate(joint(spouse={"ssn": SP_SSN, "date_of_birth": "1965-01-01", "blind": False}))
    assert d.senior_deduction == {"taxpayer_qualifies": True, "spouse_qualifies": False}


# --- ELIG-04: Standard Deduction Chart exclusions ---------------------------------------


@pytest.mark.parametrize("field, extra", [
    ("can_be_claimed_as_dependent", {}),
    ("dual_status_alien", {}),
])
def test_chart_exclusions_escalate(field, extra):
    d = evaluate(facts(**{field: True}, **extra))
    assert d.status == "escalated" and f"chart_not_applicable.{field}" in codes(d)
    assert d.computation_inputs is None


def test_separate_filer_whose_spouse_itemizes_escalates():
    d = evaluate(_mfs(spouse_itemizes=True, spouse_had_income=True, spouse_filing_return=True))
    assert "chart_not_applicable.spouse_itemizes" in codes(d)


def test_spouse_claimable_as_dependent_on_joint_return_escalates():
    d = evaluate(facts(filing_status="married_filing_jointly",
                       spouse={"ssn": SP_SSN, "date_of_birth": "1952-02-01", "blind": False},
                       spouse_can_be_claimed_as_dependent=True))
    assert "chart_not_applicable.spouse_can_be_claimed_as_dependent" in codes(d)


# --- GRD-02: missing facts become questions ---------------------------------------------


def test_nothing_known_asks_for_filing_status_and_never_completes():
    d = evaluate(EligibilityFacts())
    assert d.status == "needs_facts" and d.computation_inputs is None
    fields = {m.field for m in d.missing_facts}
    assert {"filing_status", "taxpayer_dob", "taxpayer_blind", "taxpayer_ssn"} <= fields
    assert all(m.question.endswith("?") or m.question.endswith(".") for m in d.missing_facts)


def test_unanswered_chart_questions_are_asked():
    d = evaluate(facts(can_be_claimed_as_dependent=None, dual_status_alien=None))
    assert {"can_be_claimed_as_dependent", "dual_status_alien"} <= {m.field for m in d.missing_facts}


# --- FS-01 to FS-06: filing status ------------------------------------------------------


@pytest.mark.parametrize("status", ["single", "married_filing_jointly", "head_of_household"])
def test_spouse_died_in_2025_always_escalates(status):
    d = evaluate(joint(filing_status=status, spouse_died_in_2025=True))
    assert d.status == "escalated" and "spouse_died_in_tax_year" in codes(d)
    assert d.computation_inputs is None and d.filing_status is None
    assert next(e for e in d.escalations if e.code == "spouse_died_in_tax_year").category == "filing_status_ambiguity"


def test_spouse_death_question_is_asked():
    d = evaluate(facts(spouse_died_in_2025=None))
    assert "spouse_died_in_2025" in {m.field for m in d.missing_facts}


def test_human_resolves_spouse_death_by_choosing_the_status():
    resolution = {"code": "spouse_died_in_tax_year", "filing_status": "married_filing_jointly",
                  "resolved_by": "preparer@kushim", "resolved_at": "2026-02-01T10:00:00Z", "note": "Spouse died in May."}
    d = evaluate(joint(filing_status="single", spouse_died_in_2025=True, human_resolutions=[resolution]))
    assert d.status == "complete"
    assert d.filing_status.value == "married_filing_jointly"
    status_finding = next(x for x in d.findings if x.topic == "filing_status")
    assert status_finding.decided_by == "human" and "preparer@kushim" in status_finding.reason


def test_after_human_picks_joint_the_spouse_details_are_asked_for():
    resolution = {"code": "spouse_died_in_tax_year", "filing_status": "married_filing_jointly",
                  "resolved_by": "preparer", "resolved_at": "2026-02-01T10:00:00Z"}
    d = evaluate(facts(spouse_died_in_2025=True, human_resolutions=[resolution]))
    assert d.status == "needs_facts" and d.filing_status.value == "married_filing_jointly"
    assert {"spouse_ssn", "spouse_dob", "spouse_blind"} <= {m.field for m in d.missing_facts}


def _qss(**overrides):
    base = dict(filing_status="qualifying_surviving_spouse", spouse_died_in_2025=None, spouse_death_year=2024,
                remarried_before_end_of_2025=False, child_lived_with_you_all_year=True, paid_over_half_home_cost=True,
                dependents=[{"name": "Sam", "relationship": "son", "date_of_birth": "2010-04-04", "ssn": "903-44-5555"}])
    base.update(overrides)
    return facts(**base)


def test_valid_qualifying_surviving_spouse():
    assert evaluate(_qss()).status == "complete"


@pytest.mark.parametrize("overrides", [
    {"spouse_death_year": 2021},
    {"remarried_before_end_of_2025": True},
    {"child_lived_with_you_all_year": False},
    {"paid_over_half_home_cost": False},
    {"dependents": [{"name": "Kim", "relationship": "foster child", "date_of_birth": "2010-01-01", "ssn": "903-44-5556"}]},
])
def test_qualifying_surviving_spouse_tests_fail_to_ambiguity(overrides):
    assert "filing_status_unsupported" in codes(evaluate(_qss(**overrides)))


def test_qualifying_surviving_spouse_with_death_in_2025_escalates_as_spouse_death():
    assert "spouse_died_in_tax_year" in codes(evaluate(_qss(spouse_death_year=2025)))


def test_head_of_household_without_a_dependent_is_ambiguous():
    d = evaluate(facts(filing_status="head_of_household", paid_over_half_home_cost=True))
    assert "filing_status_unsupported" in codes(d)


def test_human_can_approve_the_status_the_taxpayer_chose():
    resolution = {"code": "filing_status_unsupported", "resolved_by": "preparer", "resolved_at": "2026-02-01T10:00:00Z"}
    d = evaluate(facts(filing_status="head_of_household", paid_over_half_home_cost=True, human_resolutions=[resolution]))
    assert d.status == "complete" and d.filing_status.value == "head_of_household"


def test_separate_filer_is_asked_whether_they_lived_apart():
    d = evaluate(_mfs(lived_apart_from_spouse_all_year=None, spouse_had_income=True, spouse_filing_return=True))
    assert "lived_apart_all_year" in {m.field for m in d.missing_facts}


# --- GRD-03: test SSNs only -------------------------------------------------------------


def test_real_looking_ssn_escalates_and_is_never_repeated():
    d = evaluate(facts(taxpayer={"ssn": "123-45-6789", "date_of_birth": "1950-05-10", "blind": False}))
    assert "non_test_ssn.taxpayer" in codes(d)
    assert "6789" not in d.model_dump_json() and "123-45" not in d.model_dump_json()


def test_out_of_scope_escalations_cannot_be_approved_away():
    resolution = {"code": "non_test_ssn.taxpayer", "resolved_by": "x", "resolved_at": "2026-02-01T10:00:00Z"}
    d = evaluate(facts(taxpayer={"ssn": "123-45-6789", "date_of_birth": "1950-05-10", "blind": False},
                       human_resolutions=[resolution]))
    assert "non_test_ssn.taxpayer" in codes(d)


def test_dependent_without_ssn_is_asked_for_one():
    d = evaluate(facts(dependents=[{"name": "Ana", "relationship": "granddaughter", "date_of_birth": "2012-01-01"}]))
    assert "dependent.1.ssn" in {m.field for m in d.missing_facts}


# --- SCOPE: documents -------------------------------------------------------------------


@pytest.mark.parametrize("doc_type", ["1099-NEC", "1099-B", "K-1", "1099-DA", "Form 8949"])
def test_out_of_scope_documents_in_the_inventory_escalate(doc_type):
    d = evaluate(facts(document_inventory=[{"type": doc_type, "count": 1}]))
    assert d.status == "escalated"
    assert any(e.category == "out_of_scope" for e in d.escalations)


def test_1099r_box_2b_checked_always_escalates():
    d = evaluate(facts(documents=[r1099(**{"2b_taxable_not_determined": True})]))
    e = next(e for e in d.escalations if e.code == "taxable_amount_not_determined")
    assert e.category == "taxable_amount_not_determined" and e.document_id == "r-1"
    assert "SCOPE-03" in e.rule_ids


@pytest.mark.parametrize("code", ["G", "1", "2", "4", "Q", "4Y", "7G"])
def test_1099r_codes_outside_scope_escalate(code):
    assert "form_1099r_code_out_of_scope" in codes(evaluate(facts(documents=[r1099(**{"7": code})])))


def test_1099r_routing_ira_vs_pension():
    ira = evaluate(facts(documents=[r1099()])).documents[0]
    pension = evaluate(facts(documents=[r1099(ira_sep_simple=False)])).documents[0]
    assert ira.form_lines[:2] == ["4a", "4b"] and pension.form_lines[:2] == ["5a", "5b"]


def test_qcd_is_in_scope_for_someone_over_70_and_a_half():
    d = evaluate(facts(documents=[r1099(**{"7": "7Y"})]))
    assert d.status == "complete" and d.documents[0].qcd is True


def test_qcd_for_someone_under_70_and_a_half_conflicts():
    d = evaluate(facts(taxpayer={"ssn": TP_SSN, "date_of_birth": "1958-03-01", "blind": False},
                       documents=[r1099(**{"7": "7Y"})]))
    assert "qcd_age_conflict" in codes(d)


def test_1099r_missing_code_is_asked_not_assumed():
    d = evaluate(facts(documents=[r1099(**{"7": None})]))
    assert "document.r-1.7" in {m.field for m in d.missing_facts}


def test_1099div_capital_gains_needing_schedule_d_escalate():
    doc = {"document_id": "div-1", "document_type": "1099-DIV", "boxes": {"1a": "500", "2b": "40"}}
    assert "capital_gains_schedule_d" in codes(evaluate(facts(documents=[doc])))


def test_1099div_capital_gain_distributions_alone_stay_in_scope():
    doc = {"document_id": "div-1", "document_type": "1099-DIV", "boxes": {"1a": "500", "2a": "40"}}
    d = evaluate(facts(documents=[doc]))
    assert d.status == "complete" and "line 7a" in d.documents[0].note


@pytest.mark.parametrize("doc", [
    {"document_id": "int-1", "document_type": "1099-INT", "boxes": {"1": "100", "6": "12.50"}},
    {"document_id": "div-1", "document_type": "1099-DIV", "boxes": {"1a": "100", "7": "3"}},
])
def test_foreign_tax_paid_escalates(doc):
    assert "foreign_tax_paid" in codes(evaluate(facts(documents=[doc])))


def test_prior_year_entered_document_escalates():
    doc = {"document_id": "int-1", "document_type": "1099-INT", "tax_year": 2024, "boxes": {"1": "100"}}
    assert "prior_year_document" in codes(evaluate(facts(documents=[doc])))


# --- ESC-02: taxpayer vs document conflicts ---------------------------------------------


def _w2(last4: str) -> dict:
    return {"document_id": "w2-1", "document_type": "W-2", "source": "document_extraction",
            "status": "extracted", "employee_ssn_last4": last4}


def test_w2_for_someone_else_conflicts():
    assert "w2_ssn_mismatch" in codes(evaluate(facts(documents=[_w2("9999")])))


def test_spouse_w2_is_fine_on_a_joint_return():
    assert evaluate(joint(documents=[_w2("3333")])).status == "complete"


def test_spouse_w2_on_a_single_return_conflicts():
    assert "w2_ssn_mismatch" in codes(evaluate(facts(documents=[_w2("3333")])))


def test_document_count_mismatch_conflicts_and_human_can_approve():
    base = dict(document_inventory=[{"type": "W-2", "count": 2}], documents_supplied=True, documents=[_w2("3456")])
    assert "document_count_mismatch" in codes(evaluate(facts(**base)))
    approval = {"code": "document_count_mismatch", "document_id": "W-2",
                "resolved_by": "preparer", "resolved_at": "2026-02-01T10:00:00Z", "note": "Second W-2 was a duplicate."}
    d = evaluate(facts(**base, human_resolutions=[approval]))
    assert d.status == "complete"
    assert any(x.decided_by == "human" and x.topic.startswith("escalation.document_count_mismatch") for x in d.findings)


# --- SCH-01: schedules ------------------------------------------------------------------


def test_schedule_b_waits_for_computation_then_decides():
    def b(d):
        return next(s for s in d.schedules if s.schedule == "Schedule B").applies

    assert b(evaluate(facts())) is None
    assert b(evaluate(facts(computed_lines={"2b": 1_600, "3b": 0}))) is True
    assert b(evaluate(facts(computed_lines={"2b": 1_500, "3b": 1_500}))) is False


def test_schedules_entered_by_taxpayer_apply_and_out_of_scope_ones_never():
    d = evaluate(facts(schedules_entered=["1", "Schedule 3"]))
    applies = {s.schedule: s.applies for s in d.schedules}
    assert applies["Schedule 1"] and applies["Schedule 3"] and not applies["Schedule 2"]
    assert not any(applies[f"Schedule {x}"] for x in "ACDE")


# --- Helpers ----------------------------------------------------------------------------


@pytest.mark.parametrize("dob, expected", [
    (date(1955, 6, 30), True),   # 70½ on 2025-12-30
    (date(1955, 7, 1), False),   # 70½ on 2026-01-01
    (date(1954, 8, 31), True),   # month-end clamp: 2025-02-28
])
def test_seventy_and_a_half(dob, expected):
    assert reached_70_and_a_half_by(dob, date(2025, 12, 31)) is expected


@pytest.mark.parametrize("raw, expected", [("7Y", {"7", "Y"}), ("Y, 7", {"7", "Y"}), ("7", {"7"}), (None, set())])
def test_distribution_code_parsing(raw, expected):
    assert parse_distribution_codes(raw) == frozenset(expected)
