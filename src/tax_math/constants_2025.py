"""Every 2025 amount tax_math uses, in one place, tagged with its rule ID (see rules.py).

Supporting another tax year means adding another file like this one.
"""

from decimal import Decimal

from .models import FilingStatus

TAX_YEAR = 2025

_SINGLE = FilingStatus.SINGLE
_JOINT = FilingStatus.MARRIED_FILING_JOINTLY
_SEPARATE = FilingStatus.MARRIED_FILING_SEPARATELY
_HEAD = FilingStatus.HEAD_OF_HOUSEHOLD
_SURVIVING = FilingStatus.QUALIFYING_SURVIVING_SPOUSE

# --- Standard deduction (SD-01, SD-02, SD-03) ---------------------------------

STANDARD_DEDUCTION_BASE: dict[FilingStatus, int] = {
    _SINGLE: 15_750,
    _SEPARATE: 15_750,
    _JOINT: 31_500,
    _SURVIVING: 31_500,
    _HEAD: 23_625,
}
# Per box checked on line 12d.
ADDITIONAL_PER_BOX_UNMARRIED = 2_000  # single, head of household
ADDITIONAL_PER_BOX_MARRIED = 1_600  # married (jointly or separately), surviving spouse
UNMARRIED_STATUSES = frozenset({_SINGLE, _HEAD})
# The spouse boxes on line 12d only exist when there is a spouse on the return.
SPOUSE_BOX_STATUSES = frozenset({_JOINT, _SEPARATE})

# --- Enhanced deduction for seniors, Schedule 1-A Part V (S1A-01, S1A-02) -----

SENIOR_DEDUCTION_PER_PERSON = 6_000
SENIOR_PHASEOUT_RATE = Decimal("0.06")
SENIOR_PHASEOUT_THRESHOLD_JOINT = 150_000
SENIOR_PHASEOUT_THRESHOLD_OTHER = 75_000

# --- Taxable Social Security, Pub. 915 Worksheet 1 (SSA-01, SSA-02, SSA-03) ----

SS_BASE_AMOUNT_JOINT = 32_000
SS_BASE_AMOUNT_OTHER = 25_000
# Worksheet line 11: the gap up to the second tier ($44,000 joint, $34,000 other).
SS_SECOND_TIER_GAP_JOINT = 12_000
SS_SECOND_TIER_GAP_OTHER = 9_000
SS_HALF = Decimal("0.50")
SS_MAX_TAXABLE_SHARE = Decimal("0.85")

# --- 2025 Tax Rate Schedules (TAX-01) -----------------------------------------
# Each bracket is (top of bracket, rate). The last bracket has no top.

Bracket = tuple[int | None, Decimal]


def _schedule(*brackets: tuple[int | None, str]) -> tuple[Bracket, ...]:
    return tuple((top, Decimal(rate)) for top, rate in brackets)


SCHEDULE_X_SINGLE = _schedule(
    (11_925, "0.10"), (48_475, "0.12"), (103_350, "0.22"), (197_300, "0.24"),
    (250_525, "0.32"), (626_350, "0.35"), (None, "0.37"),
)
SCHEDULE_Y1_JOINT = _schedule(
    (23_850, "0.10"), (96_950, "0.12"), (206_700, "0.22"), (394_600, "0.24"),
    (501_050, "0.32"), (751_600, "0.35"), (None, "0.37"),
)
SCHEDULE_Y2_SEPARATE = _schedule(
    (11_925, "0.10"), (48_475, "0.12"), (103_350, "0.22"), (197_300, "0.24"),
    (250_525, "0.32"), (375_800, "0.35"), (None, "0.37"),
)
SCHEDULE_Z_HEAD = _schedule(
    (17_000, "0.10"), (64_850, "0.12"), (103_350, "0.22"), (197_300, "0.24"),
    (250_500, "0.32"), (626_350, "0.35"), (None, "0.37"),
)

RATE_SCHEDULES: dict[FilingStatus, tuple[str, tuple[Bracket, ...]]] = {
    _SINGLE: ("Schedule X", SCHEDULE_X_SINGLE),
    _JOINT: ("Schedule Y-1", SCHEDULE_Y1_JOINT),
    _SURVIVING: ("Schedule Y-1", SCHEDULE_Y1_JOINT),
    _SEPARATE: ("Schedule Y-2", SCHEDULE_Y2_SEPARATE),
    _HEAD: ("Schedule Z", SCHEDULE_Z_HEAD),
}
