"""Review checks. Each one tries to prove the return wrong.

Every check takes a ReviewContext and returns a list of Findings. Checks are
independent of each other and read-only, so they can be run in any order and
one failing check can never hide another.

Draft return shape Review expects (agree this with Computation, see
docs/IMPLEMENTATION_NOTES.md):

    draft_return = {
        "status": "draft",
        "tax_year": 2025,
        "filing_status": "married_filing_jointly",
        "taxpayer": {"dob": "1958-03-02"},
        "spouse":   {"dob": "1960-07-19"} | None,
        "boxes_12d": {"taxpayer_65": True, "taxpayer_blind": False,
                      "spouse_65": True, "spouse_blind": False},
        "scope_facts": {"claimed_as_dependent": False, "spouse_itemizes": False,
                        "dual_status_alien": False, "itemizing": False,
                        "out_of_scope_flags": []},
        "notes": [],
        "lines": {"1z": <LineRecord>, ...},
    }

    LineRecord = {
        "value": 52000,                       # whole dollars, as printed
        "source": "tool" | "user_input" | "extracted",
        # tool lines:
        "tool": "sum_amounts", "inputs": {...}, "steps": [...], "citations": [...],
        # user_input lines:
        "entered_by": "taxpayer", "entered_at": "2026-10-25T14:03:00Z",
        # extracted lines:
        "documents": ["w2-1"],
    }
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from .schemas import BLOCKER, HUMAN, WARNING, Finding
from .tools import ToolCaller

# --------------------------------------------------------------------------
# Context and small helpers
# --------------------------------------------------------------------------


@dataclass
class ReviewContext:
    ret: dict[str, Any]
    documents: list[dict[str, Any]] = field(default_factory=list)
    extracted: list[dict[str, Any]] = field(default_factory=list)
    escalations: list[dict[str, Any]] = field(default_factory=list)
    tool_caller: ToolCaller | None = None

    @property
    def lines(self) -> dict[str, dict[str, Any]]:
        return self.ret.get("lines", {}) or {}


def _dec(x: Any) -> Decimal | None:
    if isinstance(x, bool):
        return None
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError):
        return None


def _val(ctx: ReviewContext, key: str) -> Decimal | None:
    rec = ctx.lines.get(key)
    return _dec(rec.get("value")) if isinstance(rec, dict) else None


def _fmt(d: Decimal | None) -> str | None:
    if d is None:
        return None
    s = f"{d:f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _route_for(rec: dict[str, Any] | None) -> str:
    """Who produced a line is who should fix it."""
    src = (rec or {}).get("source")
    return {"tool": "computation", "user_input": "intake", "extracted": "extraction"}.get(src, "computation")


def _f(check: str, line: str | None, route: str, msg: str, *, exp=None, act=None, sev: str = BLOCKER,
       doc: str | None = None) -> Finding:
    return Finding(check, line, sev, route, msg, _fmt(exp) if isinstance(exp, Decimal) else exp,
                   _fmt(act) if isinstance(act, Decimal) else act, doc)


# --------------------------------------------------------------------------
# R01  Return is a draft, nothing was filed
# --------------------------------------------------------------------------


def check_draft_status(ctx: ReviewContext) -> list[Finding]:
    out = []
    if ctx.ret.get("status") != "draft":
        out.append(_f("R01-draft-status", None, HUMAN,
                      "The return is not marked as a draft. SilverFile must never file anything.",
                      exp="draft", act=str(ctx.ret.get("status"))))
    if ctx.ret.get("filed") or ctx.ret.get("submitted_to_irs"):
        out.append(_f("R01-draft-status", "filed", HUMAN, "The return is marked as filed or submitted."))
    return out


# --------------------------------------------------------------------------
# R02  Every dollar amount has a provenance record that holds together
# --------------------------------------------------------------------------

_NON_NEGATIVE = {"12e", "13b", "14", "15", "16", "24", "33", "34", "37"}


def check_provenance(ctx: ReviewContext) -> list[Finding]:
    out: list[Finding] = []
    for key, rec in ctx.lines.items():
        if not isinstance(rec, dict):
            out.append(_f("R02-provenance", key, "computation", f"Line {key} has no record, only a bare value."))
            continue
        value = _dec(rec.get("value"))
        if value is None:
            out.append(_f("R02-provenance", key, _route_for(rec), f"Line {key} has no usable dollar value."))
            continue
        if value != value.to_integral_value():
            out.append(_f("R02-provenance", key, _route_for(rec),
                          f"Line {key} is not in whole dollars, as it would be printed.", act=value))
        if key in _NON_NEGATIVE and value < 0:
            out.append(_f("R02-provenance", key, _route_for(rec), f"Line {key} is negative.", act=value))

        src = rec.get("source")
        if src == "tool":
            steps = rec.get("steps") or []
            if not rec.get("tool") or not isinstance(rec.get("inputs"), dict):
                out.append(_f("R02-provenance", key, "computation", f"Line {key} does not record which tool made it or its inputs."))
            if not steps:
                out.append(_f("R02-provenance", key, "computation", f"Line {key} has no working to show a reader."))
            else:
                last = _dec(steps[-1].get("amount")) if isinstance(steps[-1], dict) else None
                if last is None or last != value:
                    out.append(_f("R02-provenance", key, "computation",
                                  f"The working for line {key} does not end at the number on the form.",
                                  exp=value, act=last))
            if not rec.get("citations"):
                out.append(_f("R02-provenance", key, "computation", f"Line {key} cites no rule.", sev=WARNING))
        elif src == "user_input":
            if not rec.get("entered_by") or not rec.get("entered_at"):
                out.append(_f("R02-provenance", key, "intake",
                              f"Line {key} was typed in but does not say who entered it and when."))
        elif src == "extracted":
            if not rec.get("documents"):
                out.append(_f("R02-provenance", key, "extraction", f"Line {key} names no source document."))
        else:
            out.append(_f("R02-provenance", key, "computation",
                          f"Line {key} has an unknown or missing source ({src!r})."))
    return out


# --------------------------------------------------------------------------
# R03  Replay every tool-made line through the same tool
# --------------------------------------------------------------------------


def check_replay(ctx: ReviewContext) -> list[Finding]:
    tool_lines = {k: r for k, r in ctx.lines.items() if isinstance(r, dict) and r.get("source") == "tool"}
    if not tool_lines:
        return []
    if ctx.tool_caller is None:
        return [_f("R03-replay", None, HUMAN,
                   "Tool results could not be re-run, so those lines are unchecked.", sev=WARNING)]
    out = []
    for key, rec in tool_lines.items():
        shown = _dec(rec.get("value"))
        tool, inputs = rec.get("tool"), rec.get("inputs")
        if not tool or not isinstance(inputs, dict):
            continue  # already reported by R02
        try:
            fresh = _dec(ctx.tool_caller(tool, inputs).get("value"))
        except Exception as exc:  # a tool error is itself a finding, never a crash
            out.append(_f("R03-replay", key, "computation",
                          f"Re-running {tool} for line {key} failed: {type(exc).__name__}."))
            continue
        if fresh is None or shown is None or fresh != shown:
            out.append(_f("R03-replay", key, "computation",
                          f"Re-running {tool} gives a different answer for line {key}.", exp=fresh, act=shown))
    return out


# --------------------------------------------------------------------------
# R04  Totals recomputed independently of the tools
# --------------------------------------------------------------------------
# Review does its own addition here on purpose: if the Computation agent's tool
# and Review's arithmetic ever disagree, a human should hear about it.


def _eff(ctx: ReviewContext, key: str) -> Decimal:
    """Value of a line, falling back to its definition for optional middle lines."""
    v = _val(ctx, key)
    if v is not None:
        return v
    if key == "18":
        return _eff(ctx, "16") + _eff(ctx, "17")
    if key == "21":
        return _eff(ctx, "19") + _eff(ctx, "20")
    if key == "22":
        return max(_eff(ctx, "18") - _eff(ctx, "21"), Decimal(0))
    return Decimal(0)


# (target, plus-lines, minus-lines, floor at zero)
_FORMULAS: list[tuple[str, list[str], list[str], bool]] = [
    ("9", ["1z", "2b", "3b", "4b", "5b", "6b", "7", "8"], [], False),
    ("11a", ["9"], ["10"], False),
    ("14", ["12e", "13a", "13b"], [], False),
    ("15", ["11a"], ["14"], True),
    ("18", ["16", "17"], [], False),
    ("21", ["19", "20"], [], False),
    ("22", ["18"], ["21"], True),
    ("24", ["22", "23"], [], False),
    ("25d", ["25a", "25b", "25c"], [], False),
    ("33", ["25d", "26", "32"], [], False),
    ("34", ["33"], ["24"], True),
    ("37", ["24"], ["33"], True),
]


def check_arithmetic(ctx: ReviewContext) -> list[Finding]:
    out = []
    for target, plus, minus, floor in _FORMULAS:
        shown = _val(ctx, target)
        if shown is None:
            continue
        expected = sum((_eff(ctx, k) for k in plus), Decimal(0)) - sum((_eff(ctx, k) for k in minus), Decimal(0))
        if floor:
            expected = max(expected, Decimal(0))
        if expected != shown:
            out.append(_f("R04-arithmetic", target, _route_for(ctx.lines.get(target)),
                          f"Line {target} does not equal the lines it is built from.", exp=expected, act=shown))
    l34, l37 = _val(ctx, "34"), _val(ctx, "37")
    if l34 and l37 and l34 > 0 and l37 > 0:
        out.append(_f("R04-arithmetic", "34", "computation", "Both an overpayment (34) and an amount owed (37) are shown."))
    return out


# --------------------------------------------------------------------------
# R05  Internal contradictions
# --------------------------------------------------------------------------

# (smaller line, larger line, why). "taxable can't exceed total" style rules.
_NOT_MORE_THAN = [
    ("3a", "3b", "Qualified dividends (3a) are more than ordinary dividends (3b)."),
    ("4b", "4a", "Taxable IRA distributions (4b) are more than the total (4a)."),
    ("5b", "5a", "Taxable pensions (5b) are more than the total (5a)."),
    ("6b", "6a", "Taxable Social Security (6b) is more than the benefits received (6a)."),
]


def check_contradictions(ctx: ReviewContext) -> list[Finding]:
    out = []
    for small, large, msg in _NOT_MORE_THAN:
        a, b = _val(ctx, small), _val(ctx, large)
        if a is not None and b is not None and a > b:
            out.append(_f("R05-contradiction", small, _route_for(ctx.lines.get(small)), msg, exp=f"at most {_fmt(b)}", act=a))
    a, b = _val(ctx, "6b"), _val(ctx, "6a")
    if a is not None and b is not None and b > 0:
        # At most 85% is ever taxable. Both lines are rounded to whole dollars,
        # so allow one dollar of rounding (the $0.59 case in the tax_math notes).
        cap = (b * Decimal("0.85")).to_integral_value(rounding="ROUND_HALF_UP") + 1
        if a > cap and a <= b:
            out.append(_f("R05-contradiction", "6b", "computation",
                          "Taxable Social Security (6b) is more than 85% of benefits (6a).", exp=f"at most {_fmt(cap)}", act=a))
    return out


# --------------------------------------------------------------------------
# R06  Each tool's inputs still match the final lines (ordering dependencies)
# --------------------------------------------------------------------------
# The return has an ordering dependency: line 6b needs the other income lines,
# then 6b feeds line 9, 11a, then 13b needs 11a. If Computation ran a tool
# before an upstream line was final, the recorded inputs are stale even though
# the replay in R03 passes (the tool is right, the question put to it was wrong).
# Keys are the tax_math input names; confirm against src/tax_math/server.py.

INPUT_LINKS: dict[str, dict[str, str]] = {
    "taxable_social_security": {"net_benefits": "6a", "tax_exempt_interest": "2a"},
    "senior_deduction": {"modified_agi": "11a"},
    "tax_from_rate_schedule": {"taxable_income": "15"},
}


def check_input_links(ctx: ReviewContext) -> list[Finding]:
    out = []
    for key, rec in ctx.lines.items():
        if not isinstance(rec, dict) or rec.get("source") != "tool":
            continue
        links = INPUT_LINKS.get(rec.get("tool", ""), {})
        for input_name, line in links.items():
            if input_name not in (rec.get("inputs") or {}):
                continue
            used, final = _dec(rec["inputs"][input_name]), _val(ctx, line)
            if used is not None and final is not None and used != final:
                out.append(_f("R06-stale-input", key, "computation",
                              f"Line {key} was worked out using {used:f} for line {line}, but line {line} is now {final:f}. "
                              "It was probably computed before line {0} was final.".format(line), exp=final, act=used))
    return out


# --------------------------------------------------------------------------
# R07  Eligibility, age boxes and the senior deduction, from dates of birth
# --------------------------------------------------------------------------

_BORN_BEFORE = date(1961, 1, 2)  # 65 by the end of 2025, and the 1040-SR cut-off
_JOINT = {"married_filing_jointly"}


def _dob(person: Any) -> date | None:
    try:
        return date.fromisoformat((person or {}).get("dob", ""))
    except (TypeError, ValueError):
        return None


def check_eligibility(ctx: ReviewContext) -> list[Finding]:
    out = []
    tp, sp = ctx.ret.get("taxpayer"), ctx.ret.get("spouse")
    joint = ctx.ret.get("filing_status") in _JOINT
    tp_dob = _dob(tp)
    sp_dob = _dob(sp) if joint else None
    if tp_dob is None:
        return [_f("R07-eligibility", None, "intake", "The taxpayer's date of birth is missing, so eligibility cannot be checked.")]
    if joint and sp is not None and sp_dob is None:
        out.append(_f("R07-eligibility", None, "intake", "The spouse's date of birth is missing on a joint return."))

    tp_65 = tp_dob < _BORN_BEFORE
    sp_65 = bool(sp_dob and sp_dob < _BORN_BEFORE)
    if not (tp_65 or sp_65):
        out.append(_f("R07-eligibility", None, HUMAN,
                      "Neither person was born before 2 January 1961, so Form 1040-SR is not the right form."))

    boxes = ctx.ret.get("boxes_12d") or {}
    if boxes.get("taxpayer_65") is not None and bool(boxes["taxpayer_65"]) != tp_65:
        out.append(_f("R07-eligibility", "12d", "eligibility", "The taxpayer 65-or-older box does not match the date of birth.",
                      exp=str(tp_65), act=str(bool(boxes["taxpayer_65"]))))
    if joint and boxes.get("spouse_65") is not None and bool(boxes["spouse_65"]) != sp_65:
        out.append(_f("R07-eligibility", "12d", "eligibility", "The spouse 65-or-older box does not match the date of birth.",
                      exp=str(sp_65), act=str(bool(boxes["spouse_65"]))))
    if not joint and (boxes.get("spouse_65") or boxes.get("spouse_blind")):
        out.append(_f("R07-eligibility", "12d", "eligibility", "A spouse box is checked on a return that is not joint."))

    # Senior deduction: never more than $6,000 per person aged 65+, and only for them.
    # (Spec 3.2(c). Not the same thing as the extra standard deduction on line 12d.)
    qualifiers = int(tp_65) + int(sp_65)
    l13b = _val(ctx, "13b")
    if l13b is not None and l13b > Decimal(6000) * qualifiers:
        out.append(_f("R07-eligibility", "13b", "computation",
                      f"The senior deduction is more than $6,000 for each of the {qualifiers} qualifying person(s).",
                      exp=f"at most {6000 * qualifiers}", act=l13b))
    return out


# --------------------------------------------------------------------------
# R08  Nothing out of scope slipped through
# --------------------------------------------------------------------------

OUT_OF_SCOPE_DOC_TYPES = {
    "1099-NEC", "1099-MISC", "1099-K", "1099-B", "1099-S", "1099-DA", "K-1",
    "SCHEDULE-A", "SCHEDULE-C", "SCHEDULE-D", "SCHEDULE-E", "1040-X", "FBAR", "8938",
}
_FLAG_REASONS = {
    "claimed_as_dependent": "The taxpayer can be claimed as someone else's dependent (the standard deduction chart does not apply).",
    "spouse_itemizes": "A spouse filing separately itemizes (the standard deduction chart does not apply).",
    "dual_status_alien": "The taxpayer was a dual-status alien (the standard deduction chart does not apply).",
    "itemizing": "Itemized deductions (Schedule A) are out of scope.",
}


def _norm_type(t: Any) -> str:
    return str(t or "").upper().replace(" ", "-")


def check_scope(ctx: ReviewContext) -> list[Finding]:
    out = []
    facts = ctx.ret.get("scope_facts") or {}
    for flag, reason in _FLAG_REASONS.items():
        if facts.get(flag):
            out.append(_f("R08-scope", flag, HUMAN, reason))
    for flag in facts.get("out_of_scope_flags") or []:
        out.append(_f("R08-scope", str(flag), HUMAN, f"An out-of-scope item was flagged earlier and is still on the return: {flag}."))

    seen = {}
    for d in list(ctx.documents) + list(ctx.extracted):
        if isinstance(d, dict):
            seen.setdefault(d.get("document_id", "?"), []).append(d)
    for doc_id, versions in seen.items():
        for d in versions:
            t = _norm_type(d.get("document_type"))
            if t in OUT_OF_SCOPE_DOC_TYPES:
                out.append(_f("R08-scope", doc_id, HUMAN, f"Document {doc_id} is a {t}, which is out of scope."))
                break
        for d in versions:
            if d.get("taxable_amount_not_determined") or (d.get("fields") or {}).get("taxable_amount_not_determined"):
                out.append(_f("R08-scope", doc_id, HUMAN,
                              f"Document {doc_id} is a 1099-R with 'taxable amount not determined' checked."))
                break
    cg = _val(ctx, "7")
    if cg:
        out.append(_f("R08-scope", "7", HUMAN, "A capital gain or loss is on the return; anything needing Schedule D is out of scope.", act=cg))
    return out


# --------------------------------------------------------------------------
# R09  Source documents behind each line are usable
# --------------------------------------------------------------------------


def check_source_documents(ctx: ReviewContext) -> list[Finding]:
    status = {d.get("document_id"): d.get("status") for d in ctx.extracted if isinstance(d, dict)}
    out = []
    for key, rec in ctx.lines.items():
        if not isinstance(rec, dict):
            continue
        for doc_id in rec.get("documents") or []:
            st = status.get(doc_id)
            if st is None:
                out.append(_f("R09-source-document", key, "extraction", f"Line {key} points to document {doc_id}, which has no extraction record."))
            elif st == "manual_entry_required":
                out.append(_f("R09-source-document", key, "intake", f"Document {doc_id} needs to be entered by hand."))
            elif st != "extracted":
                out.append(_f("R09-source-document", key, "extraction",
                              f"Line {key} uses document {doc_id}, whose status is '{st}', not 'extracted'.", doc=doc_id))
    return out


# --------------------------------------------------------------------------
# R10  No real-looking SSN anywhere (spec 3.2: only 900-999 allowed)
# --------------------------------------------------------------------------

_SSN = re.compile(r"(?<!\d)(\d{3})-?(\d{2})-?(\d{4})(?!\d)")


def _strings(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


def check_ssn(ctx: ReviewContext) -> list[Finding]:
    bad = 0
    for s in _strings(ctx.ret):
        for m in _SSN.finditer(s):
            if not m.group(1).startswith("9"):
                bad += 1
    if not bad:
        return []
    # Never echo the number: the finding is shown on a dashboard and written to traces.
    return [_f("R10-ssn", None, HUMAN,
               f"{bad} value(s) on the return look like a real Social Security number (not in the 900-999 test range). They are not repeated here.")]


# --------------------------------------------------------------------------
# R11  Open escalations, and advice leaking into the return
# --------------------------------------------------------------------------

_ADVICE = re.compile(r"\b(you should|we recommend|i recommend|better off|to save (?:on )?tax|reduce your tax|consider (?:converting|filing))\b", re.I)


def check_open_escalations(ctx: ReviewContext) -> list[Finding]:
    open_ = [e for e in ctx.escalations if isinstance(e, dict) and e.get("source_agent") != "review" and not e.get("resolved")]
    if not open_:
        return []
    codes = ", ".join(sorted({str(e.get("code", "?")) for e in open_}))
    return [_f("R11-open-escalation", None, HUMAN, f"{len(open_)} escalation(s) are still unresolved ({codes}); the return cannot be approved.")]


def check_no_advice(ctx: ReviewContext) -> list[Finding]:
    texts = [t for t in _strings(ctx.ret.get("notes"))]
    hits = [t for t in texts if _ADVICE.search(t)]
    if not hits:
        return []
    return [_f("R11-advice", "notes", HUMAN, "A note on the return reads like tax advice, which SilverFile must not give.")]


ALL_CHECKS: list[Callable[[ReviewContext], list[Finding]]] = [
    check_draft_status,
    check_provenance,
    check_replay,
    check_arithmetic,
    check_contradictions,
    check_input_links,
    check_eligibility,
    check_scope,
    check_source_documents,
    check_ssn,
    check_open_escalations,
    check_no_advice,
]
