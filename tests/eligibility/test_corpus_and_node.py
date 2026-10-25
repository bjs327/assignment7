"""Grounding (every citation resolves to data/policy.md) and the LangGraph node contract."""

import ast
import re
from pathlib import Path

import pytest

from agents.eligibility import build_eligibility_graph, cite, eligibility_node, load_policy
from agents.eligibility.corpus import UnknownRuleError, parse_policy

ENGINE = Path(__file__).resolve().parents[2] / "src" / "agents" / "eligibility" / "rules_engine.py"
RULE_ID = re.compile(r"^[A-Z0-9]+-\d+$")


def _rule_ids_in_engine() -> set[str]:
    """Every rule-ID-shaped string literal in the engine, except document names like "W-2"."""
    from agents.eligibility.rules_engine import IN_SCOPE_TYPES, OUT_OF_SCOPE_TYPES

    documents = IN_SCOPE_TYPES | set(OUT_OF_SCOPE_TYPES) | {"SCHEDULEK-1", "SSA-1042-S"}
    tree = ast.parse(ENGINE.read_text(encoding="utf-8"))
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and RULE_ID.match(n.value) and n.value not in documents}


def test_every_rule_the_engine_can_cite_is_in_the_corpus():
    used = _rule_ids_in_engine()
    assert len(used) > 20
    assert used <= set(load_policy())


def test_corpus_entries_have_text_and_a_source():
    for rule in load_policy().values():
        assert rule.text and rule.source, rule.id


def test_irs_sourced_rules_link_to_irs_gov():
    for rule_id in ("ELIG-01", "ELIG-02", "ELIG-04", "FS-05", "SCOPE-03", "SCOPE-04"):
        assert "irs.gov" in cite(rule_id).source


def test_unknown_citation_raises():
    with pytest.raises(UnknownRuleError):
        cite("NOPE-99")


def test_parser_splits_rules_and_sources():
    rules = parse_policy("## A\n\n### X-01: First\n\nBody one.\n\nSource: Somewhere.\n\n### X-02: Second\n\nBody two.\n")
    assert rules["X-01"].text == "Body one." and rules["X-01"].source == "Somewhere."
    assert rules["X-02"].source == ""


# --- The node ---------------------------------------------------------------------------

INTAKE_RECORD = {  # the shape of Intake's IntakeRecord.model_dump(mode="json")
    "tax_year": 2025,
    "filing_status": "married_filing_jointly",
    "lived_apart_from_spouse_all_year": None,
    "taxpayer": {"ssn": "900-12-3456", "date_of_birth": "1950-05-10", "legally_blind": False},
    "spouse": {"ssn": "901-22-3333", "date_of_birth": "1952-02-01", "legally_blind": True},
    "dependents": [],
    "documents": [{"type": "W-2", "count": 1, "in_scope": True, "description": None}],
    "estimated_payments": [{"amount": "500"}],
    "confirmed_by_taxpayer": True,
}
EXTRACTED_W2 = {
    "document_id": "w2-1", "document_type": "W-2", "status": "extracted", "attempt": 1,
    "identifiers": {"employee_ssn_masked": "XXX-XX-3456", "ssn_check": "valid_test", "tax_year": "2025"},
    "fields": {"1": {"value": "18500.00"}, "2": {"value": "1200.00"}},
}


def test_node_reads_intake_record_and_asks_for_missing_facts():
    out = eligibility_node({"intake_record": INTAKE_RECORD, "extracted_documents": [EXTRACTED_W2]})
    assert out["eligibility"]["status"] == "needs_facts"
    assert set(out["eligibility_reask"]) == {
        "spouse_died_in_2025", "can_be_claimed_as_dependent", "spouse_can_be_claimed_as_dependent", "dual_status_alien"}
    assert out["eligibility_notice"] and out["escalations"] == []


def test_node_completes_once_intake_answers_arrive():
    answers = {"spouse_died_in_2025": False, "can_be_claimed_as_dependent": False,
               "spouse_can_be_claimed_as_dependent": False, "dual_status_alien": False}
    out = eligibility_node({"intake_record": INTAKE_RECORD, "intake_answers": answers,
                            "extracted_documents": [EXTRACTED_W2]})
    e = out["eligibility"]
    assert e["status"] == "complete" and out["eligibility_reask"] == []
    assert e["computation_inputs"]["spouse_blind"] is True
    assert e["documents"][0]["form_lines"] == ["1z", "25a"]


def test_node_emits_escalations_in_the_shared_shape():
    answers = {"spouse_died_in_2025": True, "can_be_claimed_as_dependent": False,
               "spouse_can_be_claimed_as_dependent": False, "dual_status_alien": False}
    out = eligibility_node({"intake_record": INTAKE_RECORD, "intake_answers": answers})
    (esc,) = out["escalations"]
    assert esc["source_agent"] == "eligibility_rules" and esc["code"] == "spouse_died_in_tax_year"
    assert {"document_id", "reason", "attempt", "created_at", "category", "rule_ids", "resolvable"} <= set(esc)


def test_standalone_graph_runs_and_counts_attempts():
    graph = build_eligibility_graph()
    first = graph.invoke({"intake_record": INTAKE_RECORD})
    second = graph.invoke({**first})
    assert first["eligibility_attempt"] == 1 and second["eligibility_attempt"] == 2
    assert len(second["trace"]) == 2


def test_repeat_escalation_on_rerun_is_not_duplicated():
    graph = build_eligibility_graph()
    answers = {"spouse_died_in_2025": True}
    first = graph.invoke({"intake_record": INTAKE_RECORD, "intake_answers": answers})
    second = graph.invoke({**first})
    assert [e["code"] for e in second["escalations"]].count("spouse_died_in_tax_year") == 1
