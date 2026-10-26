"""Replay against the real tax_math MCP server (skipped if mcp or tax_math is missing).

Uses the example from the tax_math notes: MFJ, taxpayer and spouse 65+, line 12e = 34,700.
"""
import pathlib

import pytest

pytest.importorskip("mcp")
pytest.importorskip("tax_math")

from agents.review import make_mcp_tool_caller, run_review  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_real_server_confirms_and_catches_standard_deduction():
    caller = make_mcp_tool_caller(ROOT)
    inputs = {"filing_status": "married_filing_jointly", "taxpayer_65_or_older": True,
              "taxpayer_blind": False, "spouse_65_or_older": True}
    good = caller("standard_deduction", inputs)["value"]
    assert good == 34700

    def ret(v):
        return {"draft_return": {"status": "draft", "lines": {"12e": {
            "value": v, "source": "tool", "tool": "standard_deduction", "inputs": inputs,
            "steps": [{"amount": str(v)}], "citations": ["X"]}}}}
    ok = run_review(ret(34700), caller)[0]
    assert not any(f.check == "R03-replay" for f in ok.findings)
    bad = run_review(ret(31500 + 2000 * 2), caller)[0]   # the classic wrong answer: unmarried amount
    assert any(f.check == "R03-replay" and f.line == "12e" for f in bad.findings)
