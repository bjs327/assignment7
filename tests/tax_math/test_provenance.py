"""Every tool result must be auditable: these checks run against a sample call of each tool and branch."""

import json
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from tax_math import RULES, TAX_YEAR

from .helpers import FUNCTIONS, SAMPLES, ids

POLICY = Path(__file__).resolve().parents[2] / "data" / "policy.md"


@pytest.fixture(params=SAMPLES, ids=ids(SAMPLES))
def result(request):
    name, arguments = request.param
    return FUNCTIONS[name](**arguments)


def test_names_its_tool_and_tax_year(result):
    assert result.tool in FUNCTIONS
    assert result.tax_year == TAX_YEAR


def test_value_is_the_result_rounded_half_up(result):
    assert result.value == int(result.unrounded_value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def test_working_ends_at_the_value_on_the_form(result):
    assert result.steps[-1].amount == result.value


def test_rounding_is_cited_exactly_when_it_happens(result):
    assert ("RND-01" in result.citations) == (result.unrounded_value != result.value)


def test_cites_only_known_rules(result):
    assert set(result.citations) <= set(RULES)


def test_has_a_summary_and_json_safe_inputs(result):
    assert result.summary.endswith(".")
    json.dumps(result.inputs)


def test_every_rule_is_used_somewhere():
    used = {rule for name, arguments in SAMPLES for rule in FUNCTIONS[name](**arguments).citations}
    assert used == set(RULES)


def test_every_rule_has_an_entry_in_the_policy_corpus():
    # Citations must resolve to a heading like "### SD-01: ..." in data/policy.md.
    headings = set(re.findall(r"^### ([A-Z0-9]+-\d+):", POLICY.read_text(encoding="utf-8"), re.MULTILINE))
    assert set(RULES) <= headings
