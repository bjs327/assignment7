"""The constants must match the amounts printed in the SilverFile specification (section 3.2)."""

from decimal import Decimal

from tax_math import FilingStatus as FS
from tax_math import constants_2025 as c


def test_tax_year():
    assert c.TAX_YEAR == 2025


def test_standard_deduction_matches_spec():
    assert c.STANDARD_DEDUCTION_BASE == {
        FS.SINGLE: 15_750,
        FS.MARRIED_FILING_SEPARATELY: 15_750,
        FS.MARRIED_FILING_JOINTLY: 31_500,
        FS.QUALIFYING_SURVIVING_SPOUSE: 31_500,
        FS.HEAD_OF_HOUSEHOLD: 23_625,
    }
    assert c.ADDITIONAL_PER_BOX_UNMARRIED == 2_000
    assert c.ADDITIONAL_PER_BOX_MARRIED == 1_600
    # "Unmarried and not a surviving spouse" means single or head of household.
    assert c.UNMARRIED_STATUSES == {FS.SINGLE, FS.HEAD_OF_HOUSEHOLD}


def test_senior_deduction_matches_spec():
    assert c.SENIOR_DEDUCTION_PER_PERSON == 6_000
    assert c.SENIOR_PHASEOUT_RATE == Decimal("0.06")
    assert c.SENIOR_PHASEOUT_THRESHOLD_OTHER == 75_000
    assert c.SENIOR_PHASEOUT_THRESHOLD_JOINT == 150_000


def test_social_security_tiers_match_spec():
    assert c.SS_BASE_AMOUNT_OTHER == 25_000
    assert c.SS_BASE_AMOUNT_JOINT == 32_000
    assert c.SS_BASE_AMOUNT_OTHER + c.SS_SECOND_TIER_GAP_OTHER == 34_000
    assert c.SS_BASE_AMOUNT_JOINT + c.SS_SECOND_TIER_GAP_JOINT == 44_000
    assert c.SS_HALF == Decimal("0.50")
    assert c.SS_MAX_TAXABLE_SHARE == Decimal("0.85")


def test_rate_schedules_are_well_formed():
    for status in FS:
        _, brackets = c.RATE_SCHEDULES[status]
        tops = [top for top, _ in brackets[:-1]]
        rates = [rate for _, rate in brackets]
        assert tops == sorted(tops) and len(set(tops)) == len(tops)
        assert brackets[-1][0] is None
        assert rates == [Decimal(r) for r in ("0.10", "0.12", "0.22", "0.24", "0.32", "0.35", "0.37")]
    # A qualifying surviving spouse uses the joint schedule.
    assert c.RATE_SCHEDULES[FS.QUALIFYING_SURVIVING_SPOUSE] == c.RATE_SCHEDULES[FS.MARRIED_FILING_JOINTLY]
