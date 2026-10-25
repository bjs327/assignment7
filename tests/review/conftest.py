"""Shared fixtures: a clean, internally consistent return and a fake tax_math.

The fake tool caller really adds and subtracts (so sum/subtract lines replay
honestly) and returns fixed answers for the three rule-based tools. That keeps
these tests fast and offline; test_real_tax_math.py runs against the real server.
"""
import copy
from decimal import Decimal

import pytest


def _line_tool(value, tool, inputs, label="step"):
    return {"value": value, "source": "tool", "tool": tool, "inputs": inputs,
            "steps": [{"label": label, "working": "", "amount": str(value)}], "citations": ["X-01"]}


def _user(value):
    return {"value": value, "source": "user_input", "entered_by": "taxpayer", "entered_at": "2026-10-25T14:00:00Z"}


def _sum(value, labels_amounts):
    return _line_tool(value, "sum_amounts", {"items": [{"label": l, "amount": str(a)} for l, a in labels_amounts]})


def _sub(value, start, less, floor=False):
    return _line_tool(value, "subtract_amounts", {"start": str(start), "less": str(less), "floor_at_zero": floor})


# Fixed answers for the rule-based tools in the clean return.
FIXED = {"taxable_social_security": 5000, "standard_deduction": 17750, "senior_deduction": 6000, "tax_from_rate_schedule": 3500}


def fake_tool_caller(tool, inputs):
    if tool == "sum_amounts":
        return {"value": sum(Decimal(i["amount"]) for i in inputs["items"])}
    if tool == "subtract_amounts":
        v = Decimal(inputs["start"]) - Decimal(inputs["less"])
        return {"value": max(v, Decimal(0)) if inputs.get("floor_at_zero") else v}
    if tool in FIXED:
        return {"value": FIXED[tool]}
    raise ValueError(f"unknown tool {tool}")


def clean_return():
    """Single filer born 1958: wages 50,000, interest 100, SS benefits 20,000 (5,000 taxable)."""
    L = {
        "1z": {"value": 50000, "source": "extracted", "documents": ["w2-1"]},
        "2a": _user(0),
        "2b": _user(100),
        "6a": _user(20000),
        "6b": _line_tool(5000, "taxable_social_security",
                         {"net_benefits": "20000", "tax_exempt_interest": "0", "filing_status": "single"}),
        "10": _user(0),
        "12e": _line_tool(17750, "standard_deduction", {"filing_status": "single", "taxpayer_65_or_older": True}),
        "13b": _line_tool(6000, "senior_deduction", {"modified_agi": "55100", "filing_status": "single"}),
        "25a": {"value": 4000, "source": "extracted", "documents": ["w2-1"]},
        "26": _user(0),
    }
    L["9"] = _sum(55100, [("1z", 50000), ("2b", 100), ("6b", 5000)])
    L["11a"] = _sub(55100, 55100, 0)
    L["14"] = _sum(23750, [("12e", 17750), ("13b", 6000)])
    L["15"] = _sub(31350, 55100, 23750, True)
    L["16"] = _line_tool(3500, "tax_from_rate_schedule", {"taxable_income": "31350", "filing_status": "single"})
    L["24"] = _sum(3500, [("16", 3500)])
    L["25d"] = _sum(4000, [("25a", 4000)])
    L["33"] = _sum(4000, [("25d", 4000), ("26", 0)])
    L["34"] = _sub(500, 4000, 3500, True)
    L["37"] = _sub(0, 3500, 4000, True)
    return {
        "status": "draft", "tax_year": 2025, "filing_status": "single",
        "taxpayer": {"dob": "1958-03-02"}, "spouse": None,
        "boxes_12d": {"taxpayer_65": True, "taxpayer_blind": False, "spouse_65": False, "spouse_blind": False},
        "scope_facts": {}, "notes": [], "lines": L,
    }


def clean_state():
    return {
        "draft_return": clean_return(),
        "documents": [{"document_id": "w2-1", "document_type": "W-2", "path": "w2.jpg"}],
        "extracted_documents": [{"document_id": "w2-1", "status": "extracted"}],
        "escalations": [],
    }


@pytest.fixture
def state():
    return copy.deepcopy(clean_state())


@pytest.fixture
def caller():
    return fake_tool_caller
