"""tax_math: deterministic 2025 Form 1040-SR arithmetic for SilverFile.

No LLM and no I/O. Every function returns a CalcResult carrying the whole-dollar value,
the working, and the rule IDs it relies on. server.py exposes the functions in TOOLS
over MCP for the Computation agent.

Design notes: docs/IMPLEMENTATION_NOTES.md. Rules cited: data/policy.md.
"""

from .constants_2025 import TAX_YEAR
from .deductions import senior_deduction, standard_deduction
from .models import CalcResult, FilingStatus, LabeledAmount, Step, TaxMathInputError
from .primitives import subtract_amounts, sum_amounts
from .rules import RULES, Rule
from .social_security import taxable_social_security
from .tax import tax_from_rate_schedule

__version__ = "0.1.0"

TOOLS = (
    sum_amounts,
    subtract_amounts,
    taxable_social_security,
    standard_deduction,
    senior_deduction,
    tax_from_rate_schedule,
)

__all__ = [
    "RULES",
    "TAX_YEAR",
    "TOOLS",
    "CalcResult",
    "FilingStatus",
    "LabeledAmount",
    "Rule",
    "Step",
    "TaxMathInputError",
    "senior_deduction",
    "standard_deduction",
    "subtract_amounts",
    "sum_amounts",
    "tax_from_rate_schedule",
    "taxable_social_security",
]
