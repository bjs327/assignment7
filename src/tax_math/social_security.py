"""Line 6b: the taxable part of Social Security benefits.

Follows IRS Pub. 915 (2025) Worksheet 1 line by line, so each step's label matches the
worksheet. Line 5 of that worksheet (adoption benefits and foreign or territory income
added back) is always $0 here, because those items are out of scope and escalate.
"""

from decimal import Decimal
from typing import Annotated

from pydantic import Field

from ._common import ZERO, finish, money, round_dollars, to_bool, to_money, to_status
from .constants_2025 import (
    SS_BASE_AMOUNT_JOINT,
    SS_BASE_AMOUNT_OTHER,
    SS_HALF,
    SS_MAX_TAXABLE_SHARE,
    SS_SECOND_TIER_GAP_JOINT,
    SS_SECOND_TIER_GAP_OTHER,
)
from .models import CalcResult, FilingStatus, Step, TaxMathInputError

_CITATIONS = ["SSA-01", "SSA-02", "SSA-03"]


def taxable_social_security(
    net_benefits: Annotated[
        Decimal,
        Field(description="Line 6a: the total of box 5 on every SSA-1099 (and RRB-1099). Must not be negative."),
    ],
    other_income: Annotated[
        Decimal,
        Field(description="Form 1040 lines 1z, 2b, 3b, 4b, 5b, 7a and 8 combined: all other income, before adjustments."),
    ],
    tax_exempt_interest: Annotated[Decimal, Field(description="Line 2a: tax-exempt interest.")],
    adjustments: Annotated[
        Decimal,
        Field(
            description=(
                "Schedule 1 lines 11 to 20, 23 and 25: every adjustment to income except the "
                "student loan interest deduction (line 21)."
            )
        ),
    ],
    filing_status: Annotated[FilingStatus, Field(description="Filing status, as confirmed by Eligibility & Rules.")],
    mfs_lived_apart_all_year: Annotated[
        bool | None,
        Field(
            description=(
                "Married filing separately only (required for that status): true if you lived "
                "apart from your spouse for all of 2025, the box on line 6d."
            )
        ),
    ] = None,
) -> CalcResult:
    """Line 6b: the taxable part of Social Security benefits (Pub. 915 Worksheet 1).

    Run this only once every other income line is final: it needs lines 1z to 5b, 7a and 8
    plus the adjustments, and its result feeds line 9 and AGI. Provisional income (other
    income less adjustments, plus tax-exempt interest, plus half the benefits) is compared
    with the $25,000 base ($32,000 joint) and the $34,000 second tier ($44,000 joint). At
    most 85% of benefits is ever taxable.
    """
    benefits = to_money(net_benefits, "net_benefits", allow_negative=False)
    other = to_money(other_income, "other_income")
    exempt = to_money(tax_exempt_interest, "tax_exempt_interest", allow_negative=False)
    adjusted = to_money(adjustments, "adjustments", allow_negative=False)
    status = to_status(filing_status)
    lived_apart = (
        None if mfs_lived_apart_all_year is None else to_bool(mfs_lived_apart_all_year, "mfs_lived_apart_all_year")
    )
    separate = status is FilingStatus.MARRIED_FILING_SEPARATELY
    if separate and lived_apart is None:
        raise TaxMathInputError(
            "mfs_lived_apart_all_year",
            "required for married filing separately: did you live apart from your spouse for all of 2025?",
        )
    if not separate and lived_apart:
        raise TaxMathInputError("mfs_lived_apart_all_year", "only applies to married filing separately")

    inputs = {
        "net_benefits": benefits,
        "other_income": other,
        "tax_exempt_interest": exempt,
        "adjustments": adjusted,
        "filing_status": status,
        "mfs_lived_apart_all_year": lived_apart,
    }
    steps: list[Step] = []

    def line(number: int, label: str, working: str, amount: Decimal) -> Decimal:
        steps.append(Step(label=f"Line {number}: {label}", working=working, amount=amount))
        return amount

    def none_taxable(reason: str, summary_reason: str) -> CalcResult:
        steps.append(Step(label="Taxable benefits", working=f"{reason}, so none are taxable", amount=ZERO))
        return finish(
            "taxable_social_security",
            ZERO,
            summary=f"None of the {money(benefits)} in benefits is taxable: {summary_reason}.",
            inputs=inputs,
            steps=steps,
            citations=_CITATIONS,
        )

    l1 = line(1, "net benefits", money(benefits), benefits)
    l2 = line(2, "half of line 1", f"50% x {money(l1)}", l1 * SS_HALF)
    l3 = line(3, "all other income", money(other), other)
    l4 = line(4, "tax-exempt interest", money(exempt), exempt)
    l6 = line(
        6,
        "lines 2 + 3 + 4 (line 5 is $0: those items are out of scope)",
        f"{money(l2)} + {money(l3)} + {money(l4)}",
        l2 + l3 + l4,
    )
    l7 = line(7, "adjustments to income", money(adjusted), adjusted)
    if l7 >= l6:
        return none_taxable(
            f"Line 7 ({money(l7)}) is not less than line 6 ({money(l6)})",
            f"other income plus tax-exempt interest plus half the benefits ({money(l6)}) is not more "
            f"than adjustments to income ({money(l7)})",
        )
    l8 = line(8, "provisional income (line 6 - line 7)", f"{money(l6)} - {money(l7)}", l6 - l7)

    if separate and not lived_apart:
        base = 0
        l17 = line(
            17,
            "85% of line 8 (married filing separately and lived with your spouse, so lines 9 to 16 are skipped)",
            f"85% x {money(l8)}",
            l8 * SS_MAX_TAXABLE_SHARE,
        )
    else:
        joint = status is FilingStatus.MARRIED_FILING_JOINTLY
        base = SS_BASE_AMOUNT_JOINT if joint else SS_BASE_AMOUNT_OTHER
        gap = SS_SECOND_TIER_GAP_JOINT if joint else SS_SECOND_TIER_GAP_OTHER
        status_note = f"{status.label}, lived apart all year" if separate else status.label
        l9 = line(9, "base amount", status_note, Decimal(base))
        if l9 >= l8:
            return none_taxable(
                f"Line 9 ({money(l9)}) is not less than line 8 ({money(l8)})",
                f"provisional income of {money(l8)} is not over the {money(base)} base amount",
            )
        l10 = line(10, "line 8 - line 9", f"{money(l8)} - {money(l9)}", l8 - l9)
        l11 = line(11, "gap to the second tier", f"{money(base + gap)} - {money(base)}", Decimal(gap))
        l12 = line(12, "line 10 - line 11, not below zero", f"{money(l10)} - {money(l11)}", max(ZERO, l10 - l11))
        l13 = line(13, "smaller of line 10 or line 11", f"smaller of {money(l10)} and {money(l11)}", min(l10, l11))
        l14 = line(14, "half of line 13", f"50% x {money(l13)}", l13 * SS_HALF)
        l15 = line(15, "smaller of line 2 or line 14", f"smaller of {money(l2)} and {money(l14)}", min(l2, l14))
        l16 = line(16, "85% of line 12", f"85% x {money(l12)}", l12 * SS_MAX_TAXABLE_SHARE)
        l17 = line(17, "line 15 + line 16", f"{money(l15)} + {money(l16)}", l15 + l16)

    l18 = line(18, "85% of line 1", f"85% x {money(l1)}", l1 * SS_MAX_TAXABLE_SHARE)
    l19 = line(
        19,
        "taxable benefits, the smaller of line 17 or line 18",
        f"smaller of {money(l17)} and {money(l18)}",
        min(l17, l18),
    )

    if separate and not lived_apart:
        context = "married filing separately and lived with your spouse, so the base amount is $0"
    else:
        context = f"provisional income of {money(l8)} against a {money(base)} base amount"
    return finish(
        "taxable_social_security",
        l19,
        summary=f"{money(round_dollars(l19))} of the {money(benefits)} in benefits is taxable ({context}).",
        inputs=inputs,
        steps=steps,
        citations=_CITATIONS,
    )
