"""Line 16: tax from the 2025 Tax Rate Schedules."""

from decimal import Decimal
from typing import Annotated

from pydantic import Field

from ._common import ZERO, finish, money, percent, round_dollars, to_money, to_status
from .constants_2025 import RATE_SCHEDULES
from .models import CalcResult, FilingStatus, Step


def tax_from_rate_schedule(
    taxable_income: Annotated[Decimal, Field(description="Line 15: taxable income. Must not be negative.")],
    filing_status: Annotated[FilingStatus, Field(description="Filing status, as confirmed by Eligibility & Rules.")],
) -> CalcResult:
    """Line 16: tax on taxable income from the 2025 Tax Rate Schedules.

    Uses the rate schedules at every income level, as the SilverFile specification asks.
    The IRS Tax Table, which real returns use below $100,000, works in $50 bands and can
    differ by a few dollars. The Qualified Dividends and Capital Gain Tax Worksheet is not
    applied, so the result is not reduced for qualified dividends.
    """
    income = to_money(taxable_income, "taxable_income", allow_negative=False)
    status = to_status(filing_status)
    schedule_name, brackets = RATE_SCHEDULES[status]

    steps: list[Step] = []
    total = ZERO
    if not income:
        steps.append(Step(label="Tax", working="Taxable income is $0", amount=ZERO))
    else:
        bottom = ZERO
        for top, rate in brackets:
            slice_top = income if top is None else min(income, Decimal(top))
            portion = slice_top - bottom
            tax = portion * rate
            steps.append(
                Step(
                    label=f"{percent(rate)} bracket",
                    working=f"{percent(rate)} x {money(portion)} (income from {money(bottom)} to {money(slice_top)})",
                    amount=tax,
                )
            )
            total += tax
            if top is None or income <= top:
                break
            bottom = Decimal(top)
        steps.append(Step(label="Tax", working=" + ".join(money(step.amount) for step in steps), amount=total))

    return finish(
        "tax_from_rate_schedule",
        total,
        summary=(
            f"Tax of {money(round_dollars(total))} on {money(income)} of taxable income "
            f"({schedule_name}, {status.label.lower()})."
        ),
        inputs={"taxable_income": income, "filing_status": status},
        steps=steps,
        citations=["TAX-01"],
    )
