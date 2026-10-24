"""Data types shared by every tax_math tool."""

from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TaxMathInputError(ValueError):
    """Inputs are missing, malformed or contradict each other.

    tax_math never guesses around bad input. The Computation agent must route the
    problem back to whichever agent supplied the value.
    """

    def __init__(self, field: str, problem: str) -> None:
        self.field = field
        self.problem = problem
        super().__init__(f"{field}: {problem}")


class FilingStatus(str, Enum):
    SINGLE = "single"
    MARRIED_FILING_JOINTLY = "married_filing_jointly"
    MARRIED_FILING_SEPARATELY = "married_filing_separately"
    HEAD_OF_HOUSEHOLD = "head_of_household"
    QUALIFYING_SURVIVING_SPOUSE = "qualifying_surviving_spouse"

    @property
    def label(self) -> str:
        """Plain-English name, e.g. 'Married filing jointly'."""
        return self.value.replace("_", " ").capitalize()


class LabeledAmount(BaseModel):
    """A dollar amount plus a short note of where it came from."""

    model_config = ConfigDict(frozen=True)

    label: str = Field(
        min_length=1,
        description="Where the amount came from, e.g. 'W-2 #1 (Acme Corp) box 1'.",
    )
    amount: Decimal = Field(description="Dollar amount with at most two decimal places.")


class Step(BaseModel):
    """One line of working, in the order a person would do it on paper."""

    model_config = ConfigDict(frozen=True)

    label: str = Field(description="What this step is, e.g. 'Line 9: base amount'.")
    working: str = Field(description="The arithmetic written out, e.g. '50% x $24,000'.")
    amount: Decimal | None = Field(
        default=None,
        description="The step's result in dollars. Empty for steps that aren't a dollar amount.",
    )


class CalcResult(BaseModel):
    """What every tax_math tool returns: the number plus everything needed to audit it."""

    model_config = ConfigDict(frozen=True)

    tool: str = Field(description="Name of the tool that produced this result.")
    tax_year: int
    value: int = Field(description="Whole-dollar amount to enter on the form.")
    unrounded_value: Decimal = Field(description="The exact result before whole-dollar rounding.")
    summary: str = Field(description="One-sentence plain-English explanation of the result.")
    inputs: dict[str, Any] = Field(description="The inputs as the tool understood them.")
    steps: list[Step] = Field(description="The full working, in order.")
    citations: list[str] = Field(description="Rule IDs from tax_math.RULES that the result relies on.")
