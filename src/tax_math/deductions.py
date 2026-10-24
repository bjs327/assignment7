"""Line 12e (standard deduction) and line 13b (enhanced deduction for seniors)."""

from decimal import Decimal
from typing import Annotated

from pydantic import Field

from ._common import ZERO, finish, money, percent, plural, round_dollars, to_bool, to_money, to_status
from .constants_2025 import (
    ADDITIONAL_PER_BOX_MARRIED,
    ADDITIONAL_PER_BOX_UNMARRIED,
    SENIOR_DEDUCTION_PER_PERSON,
    SENIOR_PHASEOUT_RATE,
    SENIOR_PHASEOUT_THRESHOLD_JOINT,
    SENIOR_PHASEOUT_THRESHOLD_OTHER,
    SPOUSE_BOX_STATUSES,
    STANDARD_DEDUCTION_BASE,
    UNMARRIED_STATUSES,
)
from .models import CalcResult, FilingStatus, Step, TaxMathInputError

_STATUS_FIELD = Field(description="Filing status, as confirmed by Eligibility & Rules.")
_BOX_LABELS = {
    "taxpayer_65_or_older": "You: 65 or older",
    "taxpayer_blind": "You: blind",
    "spouse_65_or_older": "Spouse: 65 or older",
    "spouse_blind": "Spouse: blind",
}


def standard_deduction(
    filing_status: Annotated[FilingStatus, _STATUS_FIELD],
    taxpayer_65_or_older: Annotated[bool, Field(description="Line 12d box: you were born before January 2, 1961.")],
    taxpayer_blind: Annotated[bool, Field(description="Line 12d box: you are blind.")],
    spouse_65_or_older: Annotated[
        bool,
        Field(description="Line 12d box: your spouse was born before January 2, 1961. Married filing jointly or separately only."),
    ] = False,
    spouse_blind: Annotated[
        bool,
        Field(description="Line 12d box: your spouse is blind. Married filing jointly or separately only."),
    ] = False,
) -> CalcResult:
    """Line 12e: the standard deduction from the 1040-SR Standard Deduction Chart.

    Counts the boxes checked on line 12d and adds $2,000 per box (single or head of
    household) or $1,600 per box (married or qualifying surviving spouse) to the base amount
    for the filing status. Eligibility & Rules must already have ruled out the cases where
    the chart doesn't apply: the taxpayer can be claimed as a dependent, a separately filing
    spouse itemizes, or the taxpayer was a dual-status alien. Those cases escalate instead.
    """
    status = to_status(filing_status)
    inputs = {
        "filing_status": status,
        "taxpayer_65_or_older": to_bool(taxpayer_65_or_older, "taxpayer_65_or_older"),
        "taxpayer_blind": to_bool(taxpayer_blind, "taxpayer_blind"),
        "spouse_65_or_older": to_bool(spouse_65_or_older, "spouse_65_or_older"),
        "spouse_blind": to_bool(spouse_blind, "spouse_blind"),
    }
    if status not in SPOUSE_BOX_STATUSES:
        for field in ("spouse_65_or_older", "spouse_blind"):
            if inputs[field]:
                raise TaxMathInputError(
                    field, f"the spouse boxes on line 12d don't apply to {status.label.lower()}"
                )

    checked = [label for field, label in _BOX_LABELS.items() if inputs[field]]
    count = len(checked)
    base = STANDARD_DEDUCTION_BASE[status]
    if status in UNMARRIED_STATUSES:
        per_box, reason = ADDITIONAL_PER_BOX_UNMARRIED, "unmarried"
    else:
        per_box, reason = ADDITIONAL_PER_BOX_MARRIED, "married or surviving spouse"
    additional = count * per_box
    total = Decimal(base + additional)

    steps = [
        Step(label="Base amount", working=status.label, amount=Decimal(base)),
        Step(
            label="Boxes checked on line 12d",
            working=f"{plural(count, 'box', 'boxes')}: {', '.join(checked)}" if checked else "None",
        ),
        Step(
            label="Additional amount",
            working=f"{count} x {money(per_box)} ({reason})",
            amount=Decimal(additional),
        ),
        Step(label="Standard deduction", working=f"{money(base)} + {money(additional)}", amount=total),
    ]
    return finish(
        "standard_deduction",
        total,
        summary=(
            f"{status.label} with {plural(count, 'box', 'boxes')} checked on line 12d: "
            f"{money(base)} + {count} x {money(per_box)} = {money(total)}."
        ),
        inputs=inputs,
        steps=steps,
        citations=["SD-01", "SD-02", "SD-03"],
    )


def senior_deduction(
    modified_agi: Annotated[
        Decimal,
        Field(
            description=(
                "Modified AGI for Schedule 1-A. In SilverFile's scope this equals AGI (line 11a), "
                "because the foreign income exclusions that would be added back are out of scope."
            )
        ),
    ],
    filing_status: Annotated[FilingStatus, _STATUS_FIELD],
    taxpayer_qualifies: Annotated[
        bool,
        Field(description="Eligibility & Rules confirmed you qualify (65 or older by the end of 2025)."),
    ],
    spouse_qualifies: Annotated[
        bool,
        Field(description="Eligibility & Rules confirmed your spouse qualifies. Joint returns only."),
    ] = False,
) -> CalcResult:
    """Line 13b: the enhanced deduction for seniors (Schedule 1-A, Part V).

    $6,000 for each qualifying person, reduced by 6% of modified AGI over $75,000 ($150,000
    on a joint return). The reduction applies to each person separately. This is on top of
    the age boxes in the standard deduction (line 12e), not instead of them. Who qualifies is
    decided by Eligibility & Rules, not here.
    """
    agi = to_money(modified_agi, "modified_agi")
    status = to_status(filing_status)
    you = to_bool(taxpayer_qualifies, "taxpayer_qualifies")
    spouse = to_bool(spouse_qualifies, "spouse_qualifies")
    if spouse and status is not FilingStatus.MARRIED_FILING_JOINTLY:
        raise TaxMathInputError("spouse_qualifies", "the spouse amount applies only to a joint return")

    inputs = {
        "modified_agi": agi,
        "filing_status": status,
        "taxpayer_qualifies": you,
        "spouse_qualifies": spouse,
    }
    citations = ["S1A-01", "S1A-02"]
    people = [name for name, qualifies in (("you", you), ("your spouse", spouse)) if qualifies]
    if not people:
        return finish(
            "senior_deduction",
            ZERO,
            summary="No one on this return qualifies for the senior deduction, so it is $0.",
            inputs=inputs,
            steps=[
                Step(label="Qualifying people", working="None (per Eligibility & Rules)"),
                Step(label="Senior deduction", working="No qualifying people", amount=ZERO),
            ],
            citations=citations,
        )

    joint = status is FilingStatus.MARRIED_FILING_JOINTLY
    threshold = SENIOR_PHASEOUT_THRESHOLD_JOINT if joint else SENIOR_PHASEOUT_THRESHOLD_OTHER
    excess = max(ZERO, agi - threshold)
    reduction = excess * SENIOR_PHASEOUT_RATE
    per_person = max(ZERO, SENIOR_DEDUCTION_PER_PERSON - reduction)
    total = per_person * len(people)
    who = " and ".join(people)

    if excess:
        excess_working = f"{money(agi)} - {money(threshold)}"
    else:
        excess_working = f"{money(agi)} is not over {money(threshold)}"
    per_person_working = f"{money(SENIOR_DEDUCTION_PER_PERSON)} - {money(reduction)}"
    if reduction >= SENIOR_DEDUCTION_PER_PERSON:
        per_person_working += " (fully phased out, so $0)"

    steps = [
        Step(label="Modified AGI", working=money(agi), amount=agi),
        Step(label="Phase-out threshold", working=status.label, amount=Decimal(threshold)),
        Step(label="Modified AGI over the threshold", working=excess_working, amount=excess),
        Step(label="Reduction", working=f"{percent(SENIOR_PHASEOUT_RATE)} x {money(excess)}", amount=reduction),
        Step(label="Deduction per qualifying person", working=per_person_working, amount=per_person),
        Step(label="Qualifying people", working=who[0].upper() + who[1:]),
        Step(label="Senior deduction", working=f"{len(people)} x {money(per_person)}", amount=total),
    ]
    if excess:
        detail = (
            f"{money(SENIOR_DEDUCTION_PER_PERSON)} each, reduced by {percent(SENIOR_PHASEOUT_RATE)} of the "
            f"{money(excess)} of modified AGI over {money(threshold)}"
        )
    else:
        detail = (
            f"{money(SENIOR_DEDUCTION_PER_PERSON)} each, with no reduction because modified AGI is not "
            f"over {money(threshold)}"
        )
    return finish(
        "senior_deduction",
        total,
        summary=f"Senior deduction of {money(round_dollars(total))} for {who}: {detail}.",
        inputs=inputs,
        steps=steps,
        citations=citations,
    )
