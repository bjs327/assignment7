"""Plain arithmetic for form lines that are simple totals or differences.

Even line 9 (total income) goes through a tool, so the Computation agent never adds
anything itself and every line gets a provenance trail.
"""

from typing import Annotated

from pydantic import Field

from ._common import ZERO, finish, money, plural, round_dollars, to_bool, to_labeled
from .models import CalcResult, LabeledAmount, Step, TaxMathInputError


def sum_amounts(
    items: Annotated[
        list[LabeledAmount],
        Field(description="The amounts to add, each labelled with where it came from. May be empty."),
    ],
) -> CalcResult:
    """Add labelled dollar amounts and round the total to whole dollars.

    For form lines that are plain totals: 1z (every W-2 box 1), 2a, 2b, 3a, 3b, 4a, 5a, 5b,
    6a, 9, 14, 25a to 25d, 33 and so on. Cents are kept until the total is rounded, as the
    IRS instructs. An empty list totals $0: pass one only when the document inventory
    confirms there is nothing to add.
    """
    if not isinstance(items, (list, tuple)):
        raise TaxMathInputError("items", "expected a list of {label, amount}")
    checked = [to_labeled(item, f"items[{index}]") for index, item in enumerate(items)]
    total = sum((item.amount for item in checked), ZERO)

    steps = [Step(label=item.label, working=money(item.amount), amount=item.amount) for item in checked]
    if checked:
        steps.append(
            Step(label="Total", working=" + ".join(money(item.amount) for item in checked), amount=total)
        )
        summary = f"Total of {plural(len(checked), 'amount', 'amounts')}: {money(round_dollars(total))}."
    else:
        steps.append(Step(label="Total", working="Nothing to add", amount=ZERO))
        summary = "Nothing to add, so the total is $0."

    return finish(
        "sum_amounts",
        total,
        summary=summary,
        inputs={"items": checked},
        steps=steps,
        citations=[],
    )


def subtract_amounts(
    start: Annotated[LabeledAmount, Field(description="The amount to subtract from.")],
    less: Annotated[LabeledAmount, Field(description="The amount to take away.")],
    floor_at_zero: Annotated[
        bool,
        Field(description="True for lines that can't go below zero, e.g. line 15, 34 or 37."),
    ] = False,
) -> CalcResult:
    """Subtract one labelled amount from another and round the result to whole dollars.

    For form lines that are differences: 11a (total income less adjustments), 15 (AGI less
    deductions, not below zero), and 34 and 37 (call once each way with floor_at_zero to get
    the overpayment and the amount owed; at most one of them is above zero).
    """
    start = to_labeled(start, "start")
    less = to_labeled(less, "less")
    floor_at_zero = to_bool(floor_at_zero, "floor_at_zero")
    difference = start.amount - less.amount

    steps = [
        Step(label=start.label, working=money(start.amount), amount=start.amount),
        Step(label=f"Less: {less.label}", working=money(less.amount), amount=less.amount),
        Step(label="Difference", working=f"{money(start.amount)} - {money(less.amount)}", amount=difference),
    ]
    floored = floor_at_zero and difference < 0
    result = ZERO if floored else difference
    if floored:
        steps.append(
            Step(label="Not below zero", working=f"{money(difference)} is below zero, so $0", amount=ZERO)
        )

    return finish(
        "subtract_amounts",
        result,
        summary=f"{start.label} less {less.label}: {money(round_dollars(result))}"
        + (" (not below zero)." if floored else "."),
        inputs={"start": start, "less": less, "floor_at_zero": floor_at_zero},
        steps=steps,
        citations=[],
    )
