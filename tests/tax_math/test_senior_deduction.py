import pytest
from hypothesis import given
from hypothesis import strategies as st

from tax_math import FilingStatus as FS
from tax_math import TaxMathInputError, senior_deduction

from .helpers import amounts

JOINT = FS.MARRIED_FILING_JOINTLY


@pytest.mark.parametrize(
    "magi, status, you, spouse, expected",
    [
        ("60000", FS.SINGLE, True, False, 6_000),
        ("75000", FS.SINGLE, True, False, 6_000),  # at the threshold: no reduction yet
        ("80000", FS.SINGLE, True, False, 5_700),  # 6% x $5,000 = $300
        ("80123", FS.SINGLE, True, False, 5_693),  # 6% x $5,123 = $307.38, leaving $5,692.62
        ("100000", FS.HEAD_OF_HOUSEHOLD, True, False, 4_500),
        ("100000", FS.QUALIFYING_SURVIVING_SPOUSE, True, False, 4_500),  # not a joint return
        ("175000", FS.SINGLE, True, False, 0),  # fully phased out at $75,000 + $100,000
        ("200000", FS.SINGLE, True, False, 0),  # never negative
        ("-5000", FS.SINGLE, True, False, 6_000),
        ("120000", JOINT, True, True, 12_000),
        ("160000", JOINT, True, True, 10_800),  # $600 reduction for each person
        ("160000", JOINT, True, False, 5_400),
        ("160000", JOINT, False, True, 5_400),
        ("155123", JOINT, True, True, 11_385),  # 2 x $5,692.62 = $11,385.24
        ("250000", JOINT, True, True, 0),
        ("90000", JOINT, False, False, 0),
    ],
)
def test_amounts(magi, status, you, spouse, expected):
    assert senior_deduction(magi, status, you, spouse).value == expected


def test_working_shows_the_phase_out():
    result = senior_deduction("160000", JOINT, True, True)
    assert [(step.label, step.working) for step in result.steps] == [
        ("Modified AGI", "$160,000"),
        ("Phase-out threshold", "Married filing jointly"),
        ("Modified AGI over the threshold", "$160,000 - $150,000"),
        ("Reduction", "6% x $10,000"),
        ("Deduction per qualifying person", "$6,000 - $600"),
        ("Qualifying people", "You and your spouse"),
        ("Senior deduction", "2 x $5,400"),
    ]
    assert result.citations == ["S1A-01", "S1A-02"]


def test_explains_when_no_one_qualifies():
    result = senior_deduction("50000", FS.SINGLE, False)
    assert result.value == 0
    assert result.summary == "No one on this return qualifies for the senior deduction, so it is $0."


@pytest.mark.parametrize(
    "status",
    [FS.SINGLE, FS.HEAD_OF_HOUSEHOLD, FS.MARRIED_FILING_SEPARATELY, FS.QUALIFYING_SURVIVING_SPOUSE],
)
def test_spouse_amount_needs_a_joint_return(status):
    with pytest.raises(TaxMathInputError) as raised:
        senior_deduction("50000", status, True, True)
    assert raised.value.field == "spouse_qualifies"


@given(
    magi=amounts(-50_000, 400_000),
    raise_by=amounts(0, 100_000),
    status=st.sampled_from(list(FS)),
    you=st.booleans(),
    spouse=st.booleans(),
)
def test_never_rises_with_income_and_stays_in_bounds(magi, raise_by, status, you, spouse):
    spouse = spouse and status is JOINT
    lower = senior_deduction(magi, status, you, spouse).unrounded_value
    higher = senior_deduction(magi + raise_by, status, you, spouse).unrounded_value
    assert 0 <= higher <= lower <= 6_000 * (you + spouse)
