"""Shared test data: sample tool calls (all JSON-safe) and Hypothesis strategies."""

from decimal import Decimal

from hypothesis import strategies as st

import tax_math

FUNCTIONS = {fn.__name__: fn for fn in tax_math.TOOLS}


def amounts(low: int, high: int) -> st.SearchStrategy[Decimal]:
    """Dollar amounts with cents between low and high."""
    return st.decimals(
        min_value=Decimal(low), max_value=Decimal(high), places=2, allow_nan=False, allow_infinity=False
    )


def _ss(benefits, other, exempt, adjustments, status, **extra):
    return {
        "net_benefits": benefits,
        "other_income": other,
        "tax_exempt_interest": exempt,
        "adjustments": adjustments,
        "filing_status": status,
        **extra,
    }


# One call per tool and per branch inside each tool.
SAMPLES = [
    ("sum_amounts", {"items": [
        {"label": "W-2 #1 (Acme Corp) box 1", "amount": "52345.67"},
        {"label": "W-2 #2 (Beta LLC) box 1", "amount": "1200.50"},
    ]}),
    ("sum_amounts", {"items": []}),
    ("subtract_amounts", {
        "start": {"label": "Line 9", "amount": "60000"},
        "less": {"label": "Line 10", "amount": "2500"},
    }),
    ("subtract_amounts", {
        "start": {"label": "Line 11a", "amount": "20000"},
        "less": {"label": "Line 14", "amount": "37900"},
        "floor_at_zero": True,
    }),
    ("taxable_social_security", _ss("19201", "30000", "0", "0", "single")),
    ("taxable_social_security", _ss("40000", "30000", "2000", "0", "married_filing_jointly")),
    ("taxable_social_security", _ss("20000", "10000", "0", "0", "single")),
    ("taxable_social_security", _ss("10000", "1000", "0", "7000", "head_of_household")),
    ("taxable_social_security", _ss("20000", "2000", "0", "0", "married_filing_separately",
                                    mfs_lived_apart_all_year=False)),
    ("standard_deduction", {
        "filing_status": "married_filing_jointly",
        "taxpayer_65_or_older": True,
        "taxpayer_blind": False,
        "spouse_65_or_older": True,
        "spouse_blind": True,
    }),
    ("standard_deduction", {"filing_status": "single", "taxpayer_65_or_older": False, "taxpayer_blind": False}),
    ("senior_deduction", {
        "modified_agi": "155123",
        "filing_status": "married_filing_jointly",
        "taxpayer_qualifies": True,
        "spouse_qualifies": True,
    }),
    ("senior_deduction", {"modified_agi": "60000", "filing_status": "single", "taxpayer_qualifies": True}),
    ("senior_deduction", {"modified_agi": "50000", "filing_status": "head_of_household", "taxpayer_qualifies": False}),
    ("tax_from_rate_schedule", {"taxable_income": "700000", "filing_status": "single"}),
    ("tax_from_rate_schedule", {"taxable_income": "60000", "filing_status": "qualifying_surviving_spouse"}),
    ("tax_from_rate_schedule", {"taxable_income": "0", "filing_status": "head_of_household"}),
]

# Calls that pass the MCP schema but fail tax_math's own checks, with the field blamed.
ERROR_SAMPLES = [
    ("sum_amounts", {"items": [{"label": "W-2 #1 box 1", "amount": "1.234"}]}, "items[0].amount"),
    ("standard_deduction",
     {"filing_status": "single", "taxpayer_65_or_older": True, "taxpayer_blind": False, "spouse_blind": True},
     "spouse_blind"),
    ("senior_deduction",
     {"modified_agi": "50000", "filing_status": "single", "taxpayer_qualifies": True, "spouse_qualifies": True},
     "spouse_qualifies"),
    ("taxable_social_security", _ss("20000", "10000", "0", "0", "married_filing_separately"),
     "mfs_lived_apart_all_year"),
    ("tax_from_rate_schedule", {"taxable_income": "-5", "filing_status": "single"}, "taxable_income"),
]


def ids(samples: list[tuple]) -> list[str]:
    """Test IDs like 'taxable_social_security-4': the tool and its position in the list."""
    return [f"{sample[0]}-{index}" for index, sample in enumerate(samples)]
