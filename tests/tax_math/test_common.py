from decimal import Decimal

import pytest

from tax_math import TaxMathInputError
from tax_math._common import money, percent, round_dollars, to_money


@pytest.mark.parametrize(
    "value, expected",
    [
        (52345, Decimal("52345")),
        ("52345.67", Decimal("52345.67")),
        (" 100.5 ", Decimal("100.5")),
        (Decimal("0.01"), Decimal("0.01")),
        (1200.5, Decimal("1200.5")),
        ("-250.25", Decimal("-250.25")),
    ],
)
def test_accepts_plain_amounts(value, expected):
    assert to_money(value, "x") == expected


@pytest.mark.parametrize(
    "value, problem",
    [
        (True, "true/false"),
        ("$1000", "plain number"),
        ("1,000", "plain number"),
        ("NaN", "finite"),
        ("Infinity", "finite"),
        ("1.234", "two decimal places"),
        (0.1 + 0.2, "two decimal places"),  # float noise is caught, not silently kept
        ("1000000000000", "implausibly large"),
        (None, "got NoneType"),
    ],
)
def test_rejects_bad_amounts(value, problem):
    with pytest.raises(TaxMathInputError, match=problem) as raised:
        to_money(value, "wages")
    assert raised.value.field == "wages"


def test_rejects_negative_only_when_asked():
    assert to_money("-1", "x") == Decimal("-1")
    with pytest.raises(TaxMathInputError, match="must not be negative"):
        to_money("-1", "x", allow_negative=False)


@pytest.mark.parametrize(
    "amount, expected",
    [
        ("0.49", 0),
        ("0.50", 1),  # IRS: 50 cents or more rounds up
        ("2.50", 3),  # Python's round() would give 2 (banker's rounding)
        ("1192.50", 1193),
        ("9260.425", 9260),
        ("-0.50", -1),
    ],
)
def test_rounds_half_up_to_whole_dollars(amount, expected):
    assert round_dollars(Decimal(amount)) == expected


@pytest.mark.parametrize(
    "amount, shown",
    [
        (15750, "$15,750"),
        (Decimal("1192.5"), "$1,192.50"),
        (Decimal("4760.4250"), "$4,760.425"),
        (Decimal("-17900"), "-$17,900"),
        (Decimal("0.00"), "$0"),
    ],
)
def test_shows_money_plainly(amount, shown):
    assert money(amount) == shown


@pytest.mark.parametrize("rate, shown", [("0.06", "6%"), ("0.85", "85%"), ("0.10", "10%"), ("0.50", "50%")])
def test_shows_rates_as_percentages(rate, shown):
    assert percent(Decimal(rate)) == shown
