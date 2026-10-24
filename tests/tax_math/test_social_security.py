"""Expected values were worked by hand through IRS Pub. 915 (2025) Worksheet 1."""

from decimal import Decimal

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from tax_math import FilingStatus as FS
from tax_math import TaxMathInputError, taxable_social_security
from tax_math._common import round_dollars

from .helpers import amounts

S, MFJ, MFS = FS.SINGLE, FS.MARRIED_FILING_JOINTLY, FS.MARRIED_FILING_SEPARATELY
HOH, QSS = FS.HEAD_OF_HOUSEHOLD, FS.QUALIFYING_SURVIVING_SPOUSE


@pytest.mark.parametrize(
    "benefits, other, exempt, adjustments, status, lived_apart, expected",
    [
        pytest.param("20000", "10000", "0", "0", S, None, 0, id="below the base amount"),
        pytest.param("24000", "20000", "1000", "0", S, None, 4_000, id="between the tiers"),
        pytest.param("30000", "40000", "0", "0", S, None, 22_350, id="above the second tier"),
        pytest.param("30000", "100000", "0", "0", S, None, 25_500, id="capped at 85% of benefits"),
        pytest.param("40000", "30000", "2000", "0", MFJ, None, 12_800, id="joint, above the second tier"),
        pytest.param("36000", "20000", "0", "0", MFJ, None, 3_000, id="joint, between the tiers"),
        pytest.param("24000", "22000", "1000", "2000", S, None, 4_000, id="adjustments lower provisional income"),
        pytest.param("10000", "1000", "0", "7000", S, None, 0, id="adjustments exceed line 6"),
        pytest.param("20000", "10000", "0", "0", MFS, False, 17_000, id="separate, lived with spouse"),
        pytest.param("20000", "2000", "0", "0", MFS, False, 10_200, id="separate, lived with spouse, low income"),
        pytest.param("24000", "20000", "1000", "0", MFS, True, 4_000, id="separate, lived apart"),
        pytest.param("24000", "20000", "1000", "0", HOH, None, 4_000, id="head of household"),
        pytest.param("24000", "20000", "1000", "0", QSS, None, 4_000, id="qualifying surviving spouse"),
        pytest.param("0", "80000", "0", "0", S, None, 0, id="no benefits"),
        pytest.param("19201", "30000", "0", "0", S, None, 9_260, id="cents: $9,260.425"),
    ],
)
def test_worked_examples(benefits, other, exempt, adjustments, status, lived_apart, expected):
    result = taxable_social_security(benefits, other, exempt, adjustments, status, lived_apart)
    assert result.value == expected


@pytest.mark.parametrize(
    "benefits, other, status, expected",
    [
        pytest.param("20000", "15000", S, 0, id="provisional income exactly $25,000"),
        pytest.param("20000", "24000", S, 4_500, id="exactly $34,000"),
        pytest.param("20000", "24001", S, 4_501, id="$34,001: 85% of the first dollar over"),
        pytest.param("30000", "17000", MFJ, 0, id="joint, exactly $32,000"),
        pytest.param("30000", "29000", MFJ, 6_000, id="joint, exactly $44,000"),
    ],
)
def test_threshold_boundaries(benefits, other, status, expected):
    assert taxable_social_security(benefits, other, "0", "0", status).value == expected


def test_working_follows_the_worksheet():
    result = taxable_social_security("24000", "20000", "1000", "0", S)
    numbered = [step.label.split(":")[0] for step in result.steps]
    assert numbered == [f"Line {n}" for n in (1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19)]
    assert result.steps[-1].amount == Decimal("4000")
    assert result.citations == ["SSA-01", "SSA-02", "SSA-03"]


def test_separate_and_together_skips_to_line_17():
    result = taxable_social_security("20000", "2000", "0", "0", MFS, False)
    assert [step.label.split(":")[0] for step in result.steps] == [
        "Line 1", "Line 2", "Line 3", "Line 4", "Line 6", "Line 7", "Line 8", "Line 17", "Line 18", "Line 19",
    ]
    assert "base amount is $0" in result.summary


def test_explains_why_none_is_taxable():
    result = taxable_social_security("20000", "10000", "0", "0", S)
    assert result.steps[-1].working == "Line 9 ($25,000) is not less than line 8 ($20,000), so none are taxable"
    assert result.summary == (
        "None of the $20,000 in benefits is taxable: provisional income of $20,000 is not over "
        "the $25,000 base amount."
    )


def test_explains_negative_income_without_blaming_adjustments():
    result = taxable_social_security("1000", "-5000", "0", "0", S)
    assert result.value == 0
    assert result.summary == (
        "None of the $1,000 in benefits is taxable: other income plus tax-exempt interest plus half "
        "the benefits (-$4,500) is not more than adjustments to income ($0)."
    )


def test_amounts_are_stored_in_shortest_form():
    result = taxable_social_security("19201", "30000", "0", "0", S)
    assert str(result.unrounded_value) == "9260.425"  # not 9260.4250
    assert [str(step.amount) for step in result.steps[:2]] == ["19201", "9600.5"]


@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"net_benefits": "-100"}, "net_benefits"),
        ({"tax_exempt_interest": "-1"}, "tax_exempt_interest"),
        ({"adjustments": "-1"}, "adjustments"),
        ({"other_income": "1.005"}, "other_income"),
        ({"filing_status": MFS}, "mfs_lived_apart_all_year"),
        ({"mfs_lived_apart_all_year": True}, "mfs_lived_apart_all_year"),
    ],
)
def test_rejects_bad_inputs(overrides, field):
    arguments = {
        "net_benefits": "20000",
        "other_income": "10000",
        "tax_exempt_interest": "0",
        "adjustments": "0",
        "filing_status": S,
        **overrides,
    }
    with pytest.raises(TaxMathInputError) as raised:
        taxable_social_security(**arguments)
    assert raised.value.field == field


statuses = st.one_of(
    st.tuples(st.sampled_from([S, MFJ, HOH, QSS]), st.none()),
    st.tuples(st.just(MFS), st.booleans()),
)
incomes = {
    "benefits": amounts(0, 150_000),
    "other": amounts(-20_000, 500_000),
    "exempt": amounts(0, 50_000),
    "adjustments": amounts(0, 40_000),
    "status": statuses,
}


@given(**incomes)
def test_never_more_than_85_percent_of_benefits(benefits, other, exempt, adjustments, status):
    result = taxable_social_security(benefits, other, exempt, adjustments, *status)
    assert 0 <= result.unrounded_value <= benefits * Decimal("0.85")
    # Compare the lines as they appear on the form, both rounded: for $0.59 of benefits,
    # line 6b rounds to $1, which is more than $0.59 but not more than line 6a's $1.
    assert result.value <= round_dollars(benefits)


@given(**incomes, raise_by=amounts(0, 100_000))
def test_never_falls_as_other_income_rises(benefits, other, exempt, adjustments, status, raise_by):
    lower = taxable_social_security(benefits, other, exempt, adjustments, *status).unrounded_value
    higher = taxable_social_security(benefits, other + raise_by, exempt, adjustments, *status).unrounded_value
    assert higher >= lower


@given(**incomes)
def test_at_most_half_of_benefits_up_to_the_second_tier(benefits, other, exempt, adjustments, status):
    filing_status, lived_apart = status
    assume(not (filing_status is MFS and not lived_apart))  # base amount $0: there is no 50% tier
    second_tier = 44_000 if filing_status is MFJ else 34_000
    provisional = other + exempt + benefits / 2 - adjustments
    result = taxable_social_security(benefits, other, exempt, adjustments, *status)
    if provisional <= second_tier:
        assert result.unrounded_value <= benefits / 2
