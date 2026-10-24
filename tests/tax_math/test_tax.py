from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tax_math import FilingStatus as FS
from tax_math import TaxMathInputError, tax_from_rate_schedule

from .helpers import amounts

# Tax at the top of each bracket: the "$X plus N% of the excess" figures printed in the
# 2025 Tax Rate Schedules.
BRACKET_TOPS = {
    FS.SINGLE: [
        (11_925, "1192.50"), (48_475, "5578.50"), (103_350, "17651.00"),
        (197_300, "40199.00"), (250_525, "57231.00"), (626_350, "188769.75"),
    ],
    FS.MARRIED_FILING_JOINTLY: [
        (23_850, "2385.00"), (96_950, "11157.00"), (206_700, "35302.00"),
        (394_600, "80398.00"), (501_050, "114462.00"), (751_600, "202154.50"),
    ],
    FS.MARRIED_FILING_SEPARATELY: [
        (11_925, "1192.50"), (48_475, "5578.50"), (103_350, "17651.00"),
        (197_300, "40199.00"), (250_525, "57231.00"), (375_800, "101077.25"),
    ],
    FS.HEAD_OF_HOUSEHOLD: [
        (17_000, "1700.00"), (64_850, "7442.00"), (103_350, "15912.00"),
        (197_300, "38460.00"), (250_500, "55484.00"), (626_350, "187031.50"),
    ],
}
BRACKET_TOPS[FS.QUALIFYING_SURVIVING_SPOUSE] = BRACKET_TOPS[FS.MARRIED_FILING_JOINTLY]


@pytest.mark.parametrize(
    "status, income, tax",
    [(status, income, tax) for status, tops in BRACKET_TOPS.items() for income, tax in tops],
)
def test_matches_published_amounts_at_each_bracket_top(status, income, tax):
    assert tax_from_rate_schedule(income, status).unrounded_value == Decimal(tax)


@pytest.mark.parametrize(
    "income, status, expected",
    [
        ("50000", FS.SINGLE, 5_914),
        ("100000", FS.SINGLE, 16_914),
        ("700000", FS.SINGLE, 216_020),  # $216,020.25
        ("60000", FS.MARRIED_FILING_JOINTLY, 6_723),
        ("60000", FS.QUALIFYING_SURVIVING_SPOUSE, 6_723),
        ("30000", FS.HEAD_OF_HOUSEHOLD, 3_260),
        ("11925", FS.SINGLE, 1_193),  # $1,192.50 rounds up
        ("50000.50", FS.SINGLE, 5_914),  # $5,914.11
        ("0", FS.SINGLE, 0),
    ],
)
def test_amounts(income, status, expected):
    assert tax_from_rate_schedule(income, status).value == expected


def test_working_shows_each_bracket():
    result = tax_from_rate_schedule("50000", FS.SINGLE)
    assert [step.working for step in result.steps] == [
        "10% x $11,925 (income from $0 to $11,925)",
        "12% x $36,550 (income from $11,925 to $48,475)",
        "22% x $1,525 (income from $48,475 to $50,000)",
        "$1,192.50 + $4,386 + $335.50",
    ]
    assert result.summary == "Tax of $5,914 on $50,000 of taxable income (Schedule X, single)."
    assert result.citations == ["TAX-01"]


def test_rejects_negative_income():
    with pytest.raises(TaxMathInputError) as raised:
        tax_from_rate_schedule("-1", FS.SINGLE)
    assert raised.value.field == "taxable_income"


@given(income=amounts(0, 1_000_000), raise_by=amounts(0, 100_000), status=st.sampled_from(list(FS)))
def test_marginal_rate_stays_between_10_and_37_percent(income, raise_by, status):
    lower = tax_from_rate_schedule(income, status).unrounded_value
    higher = tax_from_rate_schedule(income + raise_by, status).unrounded_value
    assert Decimal("0.10") * raise_by <= higher - lower <= Decimal("0.37") * raise_by
    assert Decimal("0.10") * income <= lower <= Decimal("0.37") * income
