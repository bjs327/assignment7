from itertools import product

import pytest

from tax_math import FilingStatus as FS
from tax_math import TaxMathInputError, standard_deduction

# The 2025 Form 1040-SR Standard Deduction Chart, indexed by the number of line 12d boxes checked.
CHART = {
    FS.SINGLE: [15_750, 17_750, 19_750],
    FS.HEAD_OF_HOUSEHOLD: [23_625, 25_625, 27_625],
    FS.MARRIED_FILING_JOINTLY: [31_500, 33_100, 34_700, 36_300, 37_900],
    FS.QUALIFYING_SURVIVING_SPOUSE: [31_500, 33_100, 34_700],
    FS.MARRIED_FILING_SEPARATELY: [15_750, 17_350, 18_950, 20_550, 22_150],
}
WITH_SPOUSE = {FS.MARRIED_FILING_JOINTLY, FS.MARRIED_FILING_SEPARATELY}


def every_valid_combination():
    for status in FS:
        for boxes in product([False, True], repeat=4):
            if status in WITH_SPOUSE or not any(boxes[2:]):
                yield status, *boxes


@pytest.mark.parametrize("status, you_65, you_blind, spouse_65, spouse_blind", list(every_valid_combination()))
def test_matches_the_chart(status, you_65, you_blind, spouse_65, spouse_blind):
    result = standard_deduction(status, you_65, you_blind, spouse_65, spouse_blind)
    assert result.value == CHART[status][sum([you_65, you_blind, spouse_65, spouse_blind])]


def test_working_names_each_box():
    result = standard_deduction(FS.MARRIED_FILING_JOINTLY, True, False, True, True)
    assert result.value == 36_300
    assert result.steps[1].working == "3 boxes: You: 65 or older, Spouse: 65 or older, Spouse: blind"
    assert result.summary == (
        "Married filing jointly with 3 boxes checked on line 12d: $31,500 + 3 x $1,600 = $36,300."
    )
    assert result.citations == ["SD-01", "SD-02", "SD-03"]


def test_accepts_status_as_text():
    assert standard_deduction("head_of_household", True, False).value == 25_625


@pytest.mark.parametrize("status", [FS.SINGLE, FS.HEAD_OF_HOUSEHOLD, FS.QUALIFYING_SURVIVING_SPOUSE])
@pytest.mark.parametrize("spouse_field", ["spouse_65_or_older", "spouse_blind"])
def test_rejects_spouse_boxes_without_a_spouse(status, spouse_field):
    with pytest.raises(TaxMathInputError) as raised:
        standard_deduction(status, False, False, **{spouse_field: True})
    assert raised.value.field == spouse_field


def test_rejects_unknown_status():
    with pytest.raises(TaxMathInputError) as raised:
        standard_deduction("married", True, False)
    assert raised.value.field == "filing_status"


def test_box_answers_must_be_true_or_false():
    with pytest.raises(TaxMathInputError) as raised:
        standard_deduction(FS.SINGLE, "yes", False)
    assert raised.value.field == "taxpayer_65_or_older"
