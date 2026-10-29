"""Computation as a LangGraph node: fills in every derived line of the draft return.

        orchestrator --> computation --> orchestrator

It never does arithmetic itself. Every dollar amount on the return, including plain totals
and rounding, comes from a tax_math tool called through the MCP server, and each line keeps
that tool's result as its provenance record: the tool, the exact arguments (so Review can
replay it), the working, the rule citations and the source documents.

Lines are filled in dependency order. Taxable Social Security (6b) needs every other income
line first, and its result feeds total income (9), AGI (11a) and then the senior deduction
(13b), which uses AGI. Every run recomputes the whole return from the current state, so a
send-back from Review can never leave a stale line behind.

Output (shape agreed with Review, see src/agents/review/checks.py): ``draft_return``, plus
``computation_status`` for the Orchestrator's routing:

| status       | next hop                                                              |
|--------------|-----------------------------------------------------------------------|
| complete     | Review                                                                |
| needs_input  | the agent named in each ``computation_missing`` entry (usually Intake) |
| blocked      | Eligibility & Rules (it hasn't finished)                               |
| error        | a human: a tax_math tool refused inputs that had passed every check    |
"""

from __future__ import annotations

import operator
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from .caller import ToolCaller, ToolCallError, mcp_tool_caller
from .inputs import ENTERED_LINES, Gathered, MissingInput, gather

NODE_NAME = "computation"
TAX_YEAR = 2025

# The rule (data/policy.md) that says what each line is, cited alongside the tool's own rules.
_DOCUMENT_LINES = ("1z", "2a", "2b", "3a", "3b", "4a", "4b", "5a", "5b", "6a", "7", "25a", "25b", "25c")
_TOTAL_LINES = ("9", "11a", "14", "15", "18", "21", "22", "24", "25d", "26", "33", "34", "37")
LINE_RULES = {**dict.fromkeys(_DOCUMENT_LINES, "LINE-01"), **dict.fromkeys(_TOTAL_LINES, "LINE-02"),
              **dict.fromkeys(ENTERED_LINES, "LINE-03")}


class ComputationState(TypedDict, total=False):
    # Inputs (written by other agents)
    eligibility: dict[str, Any]
    intake_record: dict[str, Any]
    intake_answers: dict[str, Any]
    extracted_documents: list[dict[str, Any]]
    entered_documents: list[dict[str, Any]]
    entered_amounts: dict[str, dict[str, Any]]
    revision_requests: dict[str, list[dict[str, Any]]]
    # Outputs
    draft_return: dict[str, Any]
    computation_status: str
    computation_missing: list[dict[str, str]]
    computation_revision: int
    trace: Annotated[list[dict[str, Any]], operator.add]


class _Lines:
    """Builds line records, each from exactly one tax_math call."""

    def __init__(self, call: ToolCaller, g: Gathered) -> None:
        self.call, self.g, self.records = call, g, {}

    def tool(self, line: str, tool: str, arguments: dict[str, Any], **extra: Any) -> int:
        result = self.call(tool, arguments)
        citations = list(result["citations"]) + ([LINE_RULES[line]] if line in LINE_RULES else [])
        record = {"value": result["value"], "source": "tool", "tool": tool, "inputs": arguments,
                  "summary": result["summary"], "steps": result["steps"], "citations": citations}
        if self.g.extracted_ids.get(line):
            record["documents"] = self.g.extracted_ids[line]
        if self.g.entered_ids.get(line):
            record["entered_documents"] = self.g.entered_ids[line]
        self.records[line] = {**record, **{k: v for k, v in extra.items() if v}}
        return result["value"]

    def support(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """A calculation that feeds a line without being one, kept in that line's record."""
        result = self.call(tool, arguments)
        return {"tool": tool, "inputs": arguments, **{k: result[k] for k in ("value", "summary", "steps", "citations")}}

    def total(self, line: str, items: list[dict[str, str]], **extra: Any) -> int:
        return self.tool(line, "sum_amounts", {"items": items}, **extra)

    def ref(self, line: str) -> dict[str, str]:
        return {"label": f"Line {line}", "amount": str(self.records[line]["value"])}

    def refs(self, *lines: str) -> list[dict[str, str]]:
        """References to the lines that exist; optional entered lines may be absent."""
        return [self.ref(line) for line in lines if line in self.records]

    def value(self, line: str) -> str:
        return str(self.records[line]["value"])


def compute_return(g: Gathered, call: ToolCaller) -> dict[str, dict[str, Any]]:
    """Every line of the draft return, in dependency order."""
    b = _Lines(call, g)

    def entered(line: str) -> None:
        if line in g.entered:
            e = g.entered[line]
            b.total(line, [{"label": f"{ENTERED_LINES[line]}, entered by {e['entered_by']}", "amount": e["amount"]}],
                    entered_by=e["entered_by"], entered_at=e["entered_at"])

    # Income, except Social Security.
    for line in ("1z", "2a", "2b", "3a", "3b", "4a", "5a", "5b", "6a", "7"):
        b.total(line, g.amounts.get(line, []))
    if g.qcd:  # 4b: the taxable amount less what went straight to charity
        taxable = b.support("sum_amounts", {"items": g.amounts.get("4b", [])})
        charity = b.support("sum_amounts", {"items": g.qcd})
        b.tool("4b", "subtract_amounts", {
            "start": {"label": "Taxable IRA distributions", "amount": str(taxable["value"])},
            "less": {"label": "Qualified charitable distributions", "amount": str(charity["value"])},
            "floor_at_zero": True,
        }, supporting=[taxable, charity])
    else:
        b.total("4b", g.amounts.get("4b", []))
    entered("8")
    entered("10")

    # 6b needs all the income above; Pub. 915 leaves student loan interest out of the adjustments.
    other_income = b.support("sum_amounts", {"items": b.refs("1z", "2b", "3b", "4b", "5b", "7", "8")})
    supporting = [other_income]
    adjustments = b.value("10") if "10" in b.records else "0"
    if "student_loan_interest" in g.entered:
        sli = g.entered["student_loan_interest"]
        worksheet_adjustments = b.support("subtract_amounts", {
            "start": b.ref("10"),
            "less": {"label": f"{ENTERED_LINES['student_loan_interest']}, entered by {sli['entered_by']}",
                     "amount": sli["amount"]},
            "floor_at_zero": True,
        })
        supporting.append(worksheet_adjustments)
        adjustments = str(worksheet_adjustments["value"])
    ss_arguments = {"net_benefits": b.value("6a"), "other_income": str(other_income["value"]),
                    "tax_exempt_interest": b.value("2a"), "adjustments": adjustments,
                    "filing_status": g.filing_status}
    if g.filing_status == "married_filing_separately":
        ss_arguments["mfs_lived_apart_all_year"] = g.lived_apart
    b.tool("6b", "taxable_social_security", ss_arguments, supporting=supporting)

    # Total income, AGI, deductions, taxable income, tax.
    b.total("9", b.refs("1z", "2b", "3b", "4b", "5b", "6b", "7", "8"))
    no_adjustments = {"label": "Line 10 (no adjustments entered)", "amount": "0"}
    b.tool("11a", "subtract_amounts", {"start": b.ref("9"), "less": b.ref("10") if "10" in b.records else no_adjustments})
    b.tool("12e", "standard_deduction", dict(g.deduction_inputs))
    b.tool("13b", "senior_deduction", {"modified_agi": b.value("11a"), "filing_status": g.filing_status,
                                       **g.senior_inputs})
    b.total("14", b.refs("12e", "13b"))
    b.tool("15", "subtract_amounts", {"start": b.ref("11a"), "less": b.ref("14"), "floor_at_zero": True})
    b.tool("16", "tax_from_rate_schedule", {"taxable_income": b.value("15"), "filing_status": g.filing_status})
    entered("17")
    b.total("18", b.refs("16", "17"))
    entered("19")
    entered("20")
    b.total("21", b.refs("19", "20"))
    b.tool("22", "subtract_amounts", {"start": b.ref("18"), "less": b.ref("21"), "floor_at_zero": True})
    entered("23")
    b.total("24", b.refs("22", "23"))

    # Payments, then the refund or the amount owed.
    for line in ("25a", "25b", "25c"):
        b.total(line, g.amounts.get(line, []))
    b.total("25d", b.refs("25a", "25b", "25c"))
    b.total("26", g.estimated_payments)
    entered("32")
    b.total("33", b.refs("25d", "26", "32"))
    b.tool("34", "subtract_amounts", {"start": b.ref("33"), "less": b.ref("24"), "floor_at_zero": True})
    b.tool("37", "subtract_amounts", {"start": b.ref("24"), "less": b.ref("33"), "floor_at_zero": True})
    return b.records


def build_draft_return(g: Gathered, lines: dict[str, dict[str, Any]], revision: int) -> dict[str, Any]:
    inputs = g.deduction_inputs
    return {
        "status": "draft",  # SilverFile never files anything
        "tax_year": TAX_YEAR,
        "filing_status": g.filing_status,
        "taxpayer": {"dob": g.taxpayer_dob},
        "spouse": ({"dob": g.spouse_dob, **({"date_of_death": g.spouse_date_of_death} if g.spouse_date_of_death else {})}
                   if g.spouse_dob else None),
        "boxes_12d": {"taxpayer_65": inputs["taxpayer_65_or_older"], "taxpayer_blind": inputs["taxpayer_blind"],
                      "spouse_65": inputs["spouse_65_or_older"], "spouse_blind": inputs["spouse_blind"]},
        "scope_facts": g.scope_facts,
        "notes": [],
        "lines": lines,
        "computation_revision": revision,
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def run_computation(state: dict[str, Any], call: ToolCaller) -> dict[str, Any]:
    """Pure core of the node: the state update to apply."""
    revision = int(state.get("computation_revision", 0)) + 1
    eligibility = state.get("eligibility") or {}
    if eligibility.get("status") != "complete" or not eligibility.get("computation_inputs"):
        missing = [MissingInput("eligibility", "Eligibility & Rules hasn't finished, so the filing status and "
                                "deductions aren't settled.", "eligibility")]
        return _update("blocked", revision, missing=missing)

    g = gather(state)
    if g.filing_status == "married_filing_separately" and g.lived_apart is None:
        g.missing.append(MissingInput("lived_apart_from_spouse_all_year", "Married filing separately: did you "
                                      "live apart from your spouse all year? (line 6d)", "intake"))
    if g.missing:
        return _update("needs_input", revision, missing=g.missing)
    try:
        lines = compute_return(g, call)
    except ToolCallError as exc:
        return _update("error", revision, missing=[MissingInput(exc.tool, str(exc), "human")])

    requests = (state.get("revision_requests") or {}).get("computation") or []
    return _update("complete", revision, draft=build_draft_return(g, lines, revision), lines=len(lines),
                   answered=[r.get("finding_id") for r in requests])


def _update(status: str, revision: int, *, missing: list[MissingInput] = (), draft: dict | None = None,
            lines: int = 0, answered: list[str] = ()) -> dict[str, Any]:
    update: dict[str, Any] = {
        "computation_status": status,
        "computation_missing": [m.to_dict() for m in missing],
        "computation_revision": revision,
        "trace": [{"agent": NODE_NAME, "event": status, "revision": revision, "lines": lines,
                   "missing": [m.field for m in missing], "answered_findings": list(answered),
                   "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}],
    }
    if draft is not None:
        update["draft_return"] = draft
    return update


def make_computation_node(tool_caller: ToolCaller | None = None):
    """Build the graph node. Use: builder.add_node("computation", make_computation_node())."""
    call = tool_caller or mcp_tool_caller()

    def computation_node(state: dict[str, Any]) -> dict[str, Any]:
        return run_computation(state, call)

    return computation_node
