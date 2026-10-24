"""Helpers shared by the tools: checking inputs, rounding and showing money, building results."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from typing import Any

from pydantic import ValidationError

from .constants_2025 import TAX_YEAR
from .models import CalcResult, FilingStatus, LabeledAmount, Step, TaxMathInputError

ZERO = Decimal(0)
_DOLLAR = Decimal(1)
_CENT = Decimal("0.01")
_TOO_LARGE = Decimal("1e12")

# --- Checking inputs ----------------------------------------------------------


def to_money(value: Any, field: str, *, allow_negative: bool = True) -> Decimal:
    """Convert to an exact Decimal dollar amount with at most two decimal places."""
    if isinstance(value, bool):  # bool is a subclass of int: True must not become $1
        raise TaxMathInputError(field, "expected a dollar amount, got true/false")
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, int):
        amount = Decimal(value)
    elif isinstance(value, float):
        # repr() is the shortest form that round-trips, so 0.1 stays 0.1.
        amount = Decimal(repr(value))
    elif isinstance(value, str):
        try:
            amount = Decimal(value.strip())
        except InvalidOperation:
            raise TaxMathInputError(field, f"{value!r} is not a plain number (no $ signs or commas)") from None
    else:
        raise TaxMathInputError(field, f"expected a dollar amount, got {type(value).__name__}")

    # Describe the parsed amount, not the raw input, so the message is the same whether
    # the value arrived as text, a number, or a Decimal already parsed by the MCP server.
    shown = format(amount, "f")
    if not amount.is_finite():
        raise TaxMathInputError(field, f"{shown} is not a finite amount")
    if abs(amount) >= _TOO_LARGE:
        raise TaxMathInputError(field, f"{shown} is implausibly large (over $1 trillion)")
    if amount != amount.quantize(_CENT):
        raise TaxMathInputError(field, f"{shown} has more than two decimal places")
    if amount < 0 and not allow_negative:
        raise TaxMathInputError(field, f"{shown} must not be negative")
    return amount


def to_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise TaxMathInputError(field, "expected true or false")
    return value


def to_status(value: Any) -> FilingStatus:
    try:
        return FilingStatus(value)
    except ValueError:
        options = ", ".join(status.value for status in FilingStatus)
        raise TaxMathInputError(
            "filing_status", f"{value!r} is not a filing status (expected one of: {options})"
        ) from None


def to_labeled(value: Any, field: str) -> LabeledAmount:
    try:
        item = LabeledAmount.model_validate(value)
    except ValidationError as exc:
        raise TaxMathInputError(field, f"expected {{label, amount}}: {exc.errors()[0]['msg']}") from None
    to_money(item.amount, f"{field}.amount")
    return item


def jsonable(value: Any) -> Any:
    """Echo inputs back in JSON-safe form, so results compare equal after a trip through MCP."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, LabeledAmount):
        return {"label": value.label, "amount": format(value.amount, "f")}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


# --- Rounding and showing money -------------------------------------------------


def round_dollars(amount: Decimal) -> int:
    """RND-01: drop amounts under 50 cents and round 50 to 99 cents up to the next dollar."""
    return int(amount.quantize(_DOLLAR, rounding=ROUND_HALF_UP))


def money(amount: Decimal | int) -> str:
    """'$1,234', '$1,234.50' or, for unrounded working, '$4,760.425'."""
    amount = Decimal(amount)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount == amount.to_integral_value():
        body = f"{amount:,.0f}"
    elif amount == amount.quantize(_CENT):
        body = f"{amount:,.2f}"
    else:
        body = f"{amount.normalize():,f}"
    return f"{sign}${body}"


def percent(rate: Decimal) -> str:
    return f"{(rate * 100).normalize():f}%"


def plural(count: int, singular: str, plural_form: str) -> str:
    return f"{count} {singular if count == 1 else plural_form}"


# --- Building results --------------------------------------------------------------


def _shortest(amount: Decimal) -> Decimal:
    """Drop trailing zeros without switching to exponent form (normalize() alone turns 4500 into 4.5E+3)."""
    return amount.quantize(_DOLLAR) if amount == amount.to_integral_value() else amount.normalize()


def finish(
    tool: str,
    unrounded: Decimal,
    *,
    summary: str,
    inputs: dict[str, Any],
    steps: list[Step],
    citations: list[str],
) -> CalcResult:
    """Round to whole dollars (showing the rounding as a step) and package the result.

    Amounts are stored in their shortest exact form ($4,760.4250 becomes 4760.425 and
    $4,500.00 becomes 4500), so the result reads the same however a step was computed.
    """
    unrounded = _shortest(unrounded)
    value = round_dollars(unrounded)
    steps = [step if step.amount is None else step.model_copy(update={"amount": _shortest(step.amount)}) for step in steps]
    citations = list(dict.fromkeys(citations))
    if value != unrounded:
        steps.append(
            Step(
                label="Rounded to whole dollars",
                working=f"{money(unrounded)} rounds to {money(value)}",
                amount=Decimal(value),
            )
        )
        if "RND-01" not in citations:
            citations.append("RND-01")
    return CalcResult(
        tool=tool,
        tax_year=TAX_YEAR,
        value=value,
        unrounded_value=unrounded,
        summary=summary,
        inputs={name: jsonable(item) for name, item in inputs.items()},
        steps=steps,
        citations=citations,
    )
