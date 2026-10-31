"""Gather what Computation needs from shared state, and say what's missing.

Computation reads; it never asks the taxpayer anything and never guesses. A missing amount
comes back as a MissingInput naming the agent that can supply it, so the Orchestrator can
route it there. Nothing here adds, rounds or compares dollar amounts: that is tax_math's job.

Where each number comes from:

* W-2s: ``extracted_documents`` (Document Extraction), only those with status "extracted".
  Extraction's "manual_entry_required" placeholders for other forms are skipped.
* SSA-1099, 1099-INT, 1099-DIV, 1099-R: ``entered_documents`` (typed in, see
  ``agents.eligibility.facts.DocumentFacts``), keyed by box label, e.g. ``{"5": "24000.00"}``.
  A typed-in document replaces an extracted one with the same id, as in Eligibility.
  A 1099-R with code Y (qualified charitable distribution) also needs ``qcd_amount``.
* Which form line a 1099-R feeds (IRA line 4 or pension line 5): Eligibility's routing.
* Estimated payments: ``intake_record["estimated_payments"]``.
* Direct entries from Schedules 1 to 3: ``entered_amounts``, keyed by form line (see
  ENTERED_LINES), each ``{"amount": "1200.00", "entered_by": "...", "entered_at": "<ISO 8601>"}``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agents.eligibility.rules_engine import normalize_type

# Form lines a person may enter directly from Schedules 1 to 3 (spec section 3.3), plus the
# student loan interest part of line 10, which the Social Security worksheet leaves out.
ENTERED_LINES = {
    "8": "Line 8, additional income (Schedule 1, line 10)",
    "10": "Line 10, adjustments to income (Schedule 1, line 26)",
    "17": "Line 17 (Schedule 2, line 3)",
    "19": "Line 19, child tax credit or credit for other dependents",
    "20": "Line 20 (Schedule 3, line 8)",
    "23": "Line 23, other taxes (Schedule 2, line 21)",
    "32": "Line 32, other payments and refundable credits",
    "student_loan_interest": "Student loan interest deduction (Schedule 1, line 21)",
}

# (document type, box) -> form line. A 1099-R's lines depend on Eligibility's routing.
BOX_LINES: dict[str, dict[str, str]] = {
    "W-2": {"1": "1z", "2": "25a"},
    "SSA-1099": {"5": "6a", "6": "25b"},
    "1099-INT": {"1": "2b", "3": "2b", "8": "2a", "4": "25b"},
    "1099-DIV": {"1a": "3b", "1b": "3a", "2a": "7", "4": "25b"},
    "1099-R": {"1": "total", "2a": "taxable", "4": "25b"},
}
REQUIRED_BOXES = {"W-2": ["1"], "SSA-1099": ["5"], "1099-R": ["1", "2a"]}


@dataclass(frozen=True)
class MissingInput:
    field: str  # e.g. "document.1099r-1.qcd_amount"
    reason: str  # plain English, safe for the dashboard
    route_to: str  # "intake", "extraction" or "eligibility"

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "reason": self.reason, "route_to": self.route_to}


@dataclass
class Gathered:
    """Everything the line plan needs, already sorted by form line."""

    filing_status: str
    deduction_inputs: dict[str, Any]  # standard_deduction arguments (line 12d boxes)
    senior_inputs: dict[str, bool]  # senior_deduction qualifiers
    lived_apart: bool | None  # line 6d, married filing separately only
    taxpayer_dob: str | None
    spouse_dob: str | None
    spouse_date_of_death: str | None  # asked only when it decides the spouse's age box
    scope_facts: dict[str, Any]
    amounts: dict[str, list[dict[str, str]]] = field(default_factory=dict)  # line -> [{label, amount}]
    extracted_ids: dict[str, list[str]] = field(default_factory=dict)  # line -> W-2 document ids
    entered_ids: dict[str, list[str]] = field(default_factory=dict)  # line -> typed-in document ids
    qcd: list[dict[str, str]] = field(default_factory=list)  # charity parts of IRA distributions (line 4b)
    estimated_payments: list[dict[str, str]] = field(default_factory=list)
    entered: dict[str, dict[str, Any]] = field(default_factory=dict)  # ENTERED_LINES key -> entry
    missing: list[MissingInput] = field(default_factory=list)

    def add(self, line: str, label: str, amount: str, doc_id: str, *, extracted: bool) -> None:
        self.amounts.setdefault(line, []).append({"label": label, "amount": amount})
        ids = self.extracted_ids if extracted else self.entered_ids
        if doc_id not in ids.setdefault(line, []):
            ids[line].append(doc_id)


def _plain(raw: Any) -> str | None:
    """'$1,200.00' -> '1200.00'. Reformatting text, not arithmetic; tax_math checks the result."""
    if raw is None or isinstance(raw, bool):
        return None
    text = str(raw).replace("$", "").replace(",", "").strip()
    return text or None


def _extracted_value(document: dict[str, Any], box: str) -> tuple[str | None, str | None]:
    """A W-2 box value and its label. Extraction keys fields 'box1'; older fixtures use '1'."""
    fields = document.get("fields") or {}
    entry = fields.get(f"box{box}") or fields.get(box)
    if not isinstance(entry, dict):
        return None, None
    return _plain(entry.get("value")), entry.get("source_label")


def gather(state: dict[str, Any]) -> Gathered:
    eligibility = state.get("eligibility") or {}
    inputs = eligibility.get("computation_inputs") or {}
    record = state.get("intake_record") or {}
    answers = state.get("intake_answers") or {}
    taxpayer, spouse = record.get("taxpayer") or {}, record.get("spouse") or {}

    g = Gathered(
        filing_status=inputs.get("filing_status", ""),
        deduction_inputs={
            "filing_status": inputs.get("filing_status"),
            **{k: bool(inputs.get(k)) for k in ("taxpayer_65_or_older", "taxpayer_blind",
                                                  "spouse_65_or_older", "spouse_blind")},
        },
        senior_inputs={k: bool(inputs.get(k)) for k in ("taxpayer_qualifies", "spouse_qualifies")},
        lived_apart=inputs.get("lived_apart_from_spouse_all_year"),
        taxpayer_dob=taxpayer.get("date_of_birth"),
        spouse_dob=spouse.get("date_of_birth") if spouse else None,
        spouse_date_of_death=answers.get("spouse_date_of_death") if spouse else None,
        scope_facts={
            "claimed_as_dependent": bool(answers.get("can_be_claimed_as_dependent")),
            "spouse_itemizes": bool(answers.get("spouse_itemizes")),
            "dual_status_alien": bool(answers.get("dual_status_alien")),
            "itemizing": bool(answers.get("itemizing")),
            "out_of_scope_flags": [],
        },
    )

    routing = {r["document_id"]: r for r in eligibility.get("documents") or []}

    entered_ids = {d.get("document_id") for d in state.get("entered_documents") or []}
    for doc in state.get("extracted_documents") or []:
        doc_id, status = doc.get("document_id", "?"), doc.get("status")
        if doc_id in entered_ids:
            continue  # typed in by hand, which wins (as in Eligibility's facts_from_state)
        if status == "manual_entry_required":
            # Extraction's placeholder for a form it doesn't read (anything but a W-2).
            g.missing.append(MissingInput(f"document.{doc_id}", f"The amounts on {doc_id} haven't been "
                                          "entered yet.", "intake"))
            continue
        if status != "extracted":
            g.missing.append(MissingInput(f"document.{doc_id}", f"{doc_id} has not been read successfully "
                                          f"(status '{status}').", "extraction"))
            continue
        _add_document(g, doc_id, "W-2", routing, lambda box, d=doc: _extracted_value(d, box), extracted=True)

    for doc in state.get("entered_documents") or []:
        doc_id, boxes = doc.get("document_id", "?"), doc.get("boxes") or {}
        doc_type = normalize_type(str(doc.get("document_type") or ""))
        _add_document(g, doc_id, doc_type, routing,
                      lambda box, b=boxes, t=doc_type, i=doc_id: (_plain(b.get(box)), f"{t} {i} box {box}"),
                      extracted=False, boxes=boxes)

    for n, payment in enumerate(record.get("estimated_payments") or [], start=1):
        g.estimated_payments.append({"label": f"Estimated payment {n} (as reported)",
                                     "amount": _plain(payment.get("amount")) or ""})

    for key, entry in (state.get("entered_amounts") or {}).items():
        if key not in ENTERED_LINES:
            g.missing.append(MissingInput(f"entered_amounts.{key}", f"'{key}' is not a line SilverFile "
                                          "accepts as a direct entry.", "intake"))
        elif not (isinstance(entry, dict) and _plain(entry.get("amount"))
                  and entry.get("entered_by") and entry.get("entered_at")):
            g.missing.append(MissingInput(f"entered_amounts.{key}", f"{ENTERED_LINES[key]} needs an amount, "
                                          "who entered it and when.", "intake"))
        else:
            g.entered[key] = {**entry, "amount": _plain(entry["amount"])}
    if "student_loan_interest" in g.entered and "10" not in g.entered:
        g.missing.append(MissingInput("entered_amounts.10", "Student loan interest was entered, but not the "
                                      "line 10 total it belongs to.", "intake"))
    return g


def _add_document(g: Gathered, doc_id: str, doc_type: str, routing: dict[str, dict], read,
                  *, extracted: bool, boxes: dict[str, Any] | None = None) -> None:
    route = routing.get(doc_id)
    if doc_type not in BOX_LINES:
        g.missing.append(MissingInput(f"document.{doc_id}", f"{doc_id} is a {doc_type or 'document'} "
                                      "Computation doesn't handle.", "eligibility"))
        return
    if not route or not route.get("in_scope"):
        g.missing.append(MissingInput(f"document.{doc_id}", f"Eligibility & Rules hasn't cleared {doc_id} "
                                      "as in scope.", "eligibility"))
        return

    for box in REQUIRED_BOXES.get(doc_type, []):
        if read(box)[0] is None:
            g.missing.append(MissingInput(f"document.{doc_id}.{box}", f"{doc_id} box {box} is blank or "
                                          "unreadable.", "extraction" if extracted else "intake"))
    is_ira = "4a" in (route.get("form_lines") or [])
    for box, line in BOX_LINES[doc_type].items():
        amount, label = read(box)
        if amount is None:
            continue
        if line in ("total", "taxable"):
            line = ("4a" if is_ira else "5a") if line == "total" else ("4b" if is_ira else "5b")
        g.add(line, label or f"{doc_type} {doc_id} box {box}", amount, doc_id, extracted=extracted)

    if doc_type == "1099-R" and route.get("qcd"):
        qcd = _plain((boxes or {}).get("qcd_amount"))
        if qcd is None:
            g.missing.append(MissingInput(f"document.{doc_id}.qcd_amount", f"{doc_id} is a qualified charitable "
                                          "distribution: how much of it went directly to charity?", "intake"))
        else:
            g.qcd.append({"label": f"1099-R {doc_id} qualified charitable distribution", "amount": qcd})
