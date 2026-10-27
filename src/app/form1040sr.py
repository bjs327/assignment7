"""The draft Form 1040-SR, as a person reads it: every line with its value, and, one click away,
where that value came from (specification section 4.2).

Presentation only. It reads ``final_package`` (the Orchestrator's ``finalize`` output) and lays
out what Computation already wrote: the tool that produced each amount, its exact inputs, the
working, the rule citations and the source documents. It never computes or rounds an amount; a
line Computation didn't write is shown blank.

``build_form`` returns plain data (used by the dashboard and tests); ``render_html`` turns it into
one self-contained HTML page: hover a number for a one-line summary, click it for the full record.
"""

from __future__ import annotations

import html
from typing import Any

FILING_STATUS_LABELS = {
    "single": "Single",
    "married_filing_jointly": "Married filing jointly",
    "married_filing_separately": "Married filing separately",
    "head_of_household": "Head of household",
    "qualifying_surviving_spouse": "Qualifying surviving spouse",
}

# The lines SilverFile fills (specification section 3.1) and the lines between them, in form order.
SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Income", [
        ("1z", "Total wages (Form W-2, box 1)"),
        ("2a", "Tax-exempt interest"),
        ("2b", "Taxable interest"),
        ("3a", "Qualified dividends"),
        ("3b", "Ordinary dividends"),
        ("4a", "IRA distributions"),
        ("4b", "IRA distributions, taxable amount"),
        ("5a", "Pensions and annuities"),
        ("5b", "Pensions and annuities, taxable amount"),
        ("6a", "Social Security benefits"),
        ("6b", "Social Security benefits, taxable amount"),
        ("7", "Capital gain distributions"),
        ("8", "Additional income from Schedule 1, line 10"),
        ("9", "Total income"),
        ("10", "Adjustments to income from Schedule 1, line 26"),
        ("11a", "Adjusted gross income"),
    ]),
    ("Deductions and tax", [
        ("12e", "Standard deduction (Standard Deduction Chart)"),
        ("13b", "Additional deductions from Schedule 1-A (senior deduction)"),
        ("14", "Total deductions"),
        ("15", "Taxable income"),
        ("16", "Tax"),
        ("17", "Amount from Schedule 2, line 3"),
        ("18", "Add lines 16 and 17"),
        ("19", "Child tax credit or credit for other dependents"),
        ("20", "Amount from Schedule 3, line 8"),
        ("21", "Add lines 19 and 20"),
        ("22", "Subtract line 21 from line 18"),
        ("23", "Other taxes, from Schedule 2, line 21"),
        ("24", "Total tax"),
    ]),
    ("Payments", [
        ("25a", "Federal income tax withheld: Form(s) W-2"),
        ("25b", "Federal income tax withheld: Form(s) 1099"),
        ("25c", "Federal income tax withheld: other forms"),
        ("25d", "Total federal income tax withheld"),
        ("26", "Estimated tax payments"),
        ("32", "Other payments and refundable credits"),
        ("33", "Total payments"),
    ]),
    ("Refund or amount you owe", [
        ("34", "Overpaid (amount you overpaid)"),
        ("37", "Amount you owe"),
    ]),
]

BOXES_12D = [("taxpayer_65", "You were born before January 2, 1961"), ("taxpayer_blind", "You are blind"),
             ("spouse_65", "Spouse was born before January 2, 1961"), ("spouse_blind", "Spouse is blind")]


def _money(value: Any) -> str:
    try:
        return f"${int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _citations(rule_ids: list[str]) -> list[str]:
    from agents.eligibility.corpus import UnknownRuleError, cite

    out = []
    for rule_id in dict.fromkeys(rule_ids or []):
        try:
            rule = cite(rule_id)
            out.append(f"{rule.id}: {rule.title} (source: {rule.source})")
        except UnknownRuleError:
            out.append(rule_id)
    return out


def _document_sources(record: dict[str, Any], package: dict[str, Any]) -> list[str]:
    """Where each source document came from: read from an image, or typed in by a person."""
    extracted = {d.get("document_id"): d for d in package.get("extracted_documents") or []}
    entered = {d.get("document_id"): d for d in package.get("entered_documents") or []}
    out = []
    for doc_id in list(record.get("documents") or []) + list(record.get("entered_documents") or []):
        if doc_id in entered:
            d = entered[doc_id]
            who = d.get("entered_by") or "not recorded"
            when = d.get("entered_at") or "not recorded"
            out.append(f"{d.get('document_type')} {doc_id}: direct user input, typed in by {who} at {when}")
        elif doc_id in extracted:
            d = extracted[doc_id]
            fields = d.get("fields") or {}
            prov = next((f.get("provenance") for f in fields.values() if f.get("provenance")), {}) or {}
            employer = (d.get("identifiers") or {}).get("employer_name") or "employer not read"
            out.append(f"{d.get('document_type')} {doc_id} ({employer}): read from the uploaded image "
                       f"by {prov.get('method', 'Document Extraction')} at {prov.get('extracted_at', d.get('extracted_at'))}"
                       f"; file SHA-256 {str(d.get('file_sha256') or prov.get('file_sha256') or '')[:12]}...")
            used = str(record.get("inputs") or "") + str(record.get("steps") or "")
            for box, f in sorted(fields.items()):  # only the boxes this line used
                if f.get("provenance") and f.get("source_label") and f["source_label"] in used:
                    out.append(f"    {f.get('source_label')}: {_money_text(f.get('value'))} "
                               f"(printed as \"{f['provenance'].get('raw_text')}\")")
        else:
            out.append(f"{doc_id}")
    return out


def _money_text(value: Any) -> str:
    return f"${value}" if value not in (None, "") else "blank"


def explain(record: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    """Everything a person needs to check one line, from its line record."""
    steps = [f"{s.get('label')}: {s.get('working')}" for s in record.get("steps") or []]
    supporting = [f"{s.get('tool')}: {s.get('summary') or ''}".strip() for s in record.get("supporting") or []]
    entered = None
    if record.get("entered_by") or record.get("source") == "user_input":
        entered = f"Direct user input, entered by {record.get('entered_by')} at {record.get('entered_at')}"
    return {
        "summary": record.get("summary") or "",
        "source": record.get("source"),
        "tool": record.get("tool"),
        "inputs": record.get("inputs"),
        "steps": steps,
        "supporting": supporting,
        "sources": _document_sources(record, package),
        "entered": entered,
        "citations": _citations(record.get("citations") or []),
    }


def _person(p: dict[str, Any] | None) -> dict[str, Any] | None:
    if not p:
        return None
    return {"ssn": p.get("ssn"), "date_of_birth": p.get("date_of_birth"), "blind": p.get("legally_blind")}


def build_form(package: dict[str, Any]) -> dict[str, Any]:
    """The draft form as plain data, from a ``final_package``."""
    draft = package.get("draft_return") or {}
    record = package.get("intake_record") or {}
    lines = draft.get("lines") or {}
    sections = []
    for title, rows in SECTIONS:
        sections.append({"title": title, "rows": [
            {"line": line, "label": label, "value": lines[line]["value"] if line in lines else None,
             "display": _money(lines[line]["value"]) if line in lines else "",
             "explanation": explain(lines[line], package) if line in lines else None}
            for line, label in rows]})
    status = draft.get("filing_status") or record.get("filing_status")
    open_issues = [e for e in package.get("escalations") or [] if not e.get("resolved")]
    return {
        "session_id": package.get("session_id"),
        "run_status": package.get("run_status"),
        "has_draft": bool(lines),
        "tax_year": draft.get("tax_year") or record.get("tax_year") or 2025,
        "filing_status": FILING_STATUS_LABELS.get(status, status),
        "taxpayer": _person(record.get("taxpayer")),
        "spouse": _person(record.get("spouse")),
        "dependents": [{"name": d.get("name"), "ssn": d.get("ssn"), "relationship": d.get("relationship")}
                       for d in record.get("dependents") or []],
        "boxes_12d": [(label, bool((draft.get("boxes_12d") or {}).get(key))) for key, label in BOXES_12D],
        "sections": sections,
        "review": {"verdict": (package.get("review_report") or {}).get("verdict"),
                   "summary": (package.get("review_report") or {}).get("summary")},
        "human_decisions": package.get("human_decisions") or [],
        "stop_reasons": package.get("stop_reasons") or [],
        "open_issues": [e.get("reason") for e in open_issues],
        "notes": draft.get("notes") or [],
    }


# --- HTML ------------------------------------------------------------------------------------

_CSS = """
body{font-family:Georgia,'Times New Roman',serif;font-size:18px;line-height:1.5;max-width:900px;margin:24px auto;color:#111;padding:0 12px}
h1{font-size:28px;margin-bottom:0} .sub{margin-top:4px;color:#333}
.banner{border:3px solid #8a1c1c;padding:10px 14px;margin:16px 0;font-weight:bold;background:#fff6f6}
table{border-collapse:collapse;width:100%;margin:8px 0 20px}
th,td{border:1px solid #888;padding:6px 8px;vertical-align:top;text-align:left}
th{background:#eee} td.line{width:56px;font-weight:bold} td.amt{width:240px;text-align:right}
details summary{cursor:pointer;font-weight:bold;color:#0b4f8a;list-style:none}
details summary::-webkit-details-marker{display:none}
details[open] summary{text-decoration:underline}
.prov{text-align:left;font-family:Arial,Helvetica,sans-serif;font-size:15px;background:#f5f8fc;border:1px solid #c9d6e6;padding:8px;margin-top:6px}
.prov ul{margin:4px 0 4px 18px;padding:0} code{font-size:14px;word-break:break-word}
.box{display:inline-block;margin-right:18px}
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _explanation_html(x: dict[str, Any]) -> str:
    parts = [f"<div><b>How:</b> {_e(x['summary'])}</div>"] if x["summary"] else []
    if x["entered"]:
        parts.append(f"<div><b>Entered:</b> {_e(x['entered'])}</div>")
    if x["tool"]:
        parts.append(f"<div><b>Calculated by:</b> the <code>{_e(x['tool'])}</code> tax_math tool "
                     f"(deterministic MCP tool), with inputs <code>{_e(x['inputs'])}</code></div>")
    for title, key in (("Working", "steps"), ("Uses", "supporting"), ("Source documents", "sources"),
                       ("Rules", "citations")):
        if x[key]:
            items = "".join(f"<li>{_e(s)}</li>" for s in x[key])
            parts.append(f"<div><b>{title}:</b><ul>{items}</ul></div>")
    return "<div class='prov'>" + "".join(parts) + "</div>"


def render_html(form: dict[str, Any]) -> str:
    """One self-contained page. Hover a number for its summary; click it for the full record."""
    out = ["<!doctype html><html lang='en'><head><meta charset='utf-8'>",
           f"<title>Draft Form 1040-SR {form['tax_year']}: {_e(form['session_id'])}</title>",
           f"<style>{_CSS}</style></head><body>",
           f"<h1>Form 1040-SR, U.S. Income Tax Return for Seniors, {form['tax_year']}</h1>",
           f"<p class='sub'>SilverFile session {_e(form['session_id'])}. Run status: <b>{_e(form['run_status'])}</b></p>",
           "<div class='banner' role='note'>DRAFT for review by a Kushim preparer. Not filed, and not tax advice. "
           "Click any amount to see how it was worked out and where it came from.</div>"]
    if not form["has_draft"]:
        out.append("<p><b>No draft return was produced.</b> The run was handed to a preparer.</p>")
        for s in form["stop_reasons"]:
            out.append(f"<p>Stopped by {_e(s.get('decided_by'))}: {_e(s.get('note') or s.get('escalation_id'))}</p>")
    out.append("<h2>Filing status and people</h2><table>")
    out.append(f"<tr><th>Filing status</th><td>{_e(form['filing_status'])}</td></tr>")
    for who in ("taxpayer", "spouse"):
        p = form[who]
        if p:
            out.append(f"<tr><th>{who.title()} SSN (test range only)</th><td>{_e(p['ssn'])}</td></tr>")
    out.append("</table>")
    if form["dependents"]:
        out.append("<h2>Dependents</h2><table><tr><th>Name</th><th>SSN</th><th>Relationship</th></tr>")
        out += [f"<tr><td>{_e(d['name'])}</td><td>{_e(d['ssn'])}</td><td>{_e(d['relationship'])}</td></tr>"
                for d in form["dependents"]]
        out.append("</table>")
    out.append("<h2>Line 12d</h2><p>" + "".join(
        f"<span class='box'>[{'X' if checked else '&nbsp;'}] {_e(label)}</span>" for label, checked in form["boxes_12d"])
        + "</p>")
    for section in form["sections"]:
        out.append(f"<h2>{_e(section['title'])}</h2><table><tr><th>Line</th><th>Description</th><th>Amount</th></tr>")
        for row in section["rows"]:
            x = row["explanation"]
            if x is None:
                cell = "<span aria-label='blank'>&nbsp;</span>"
            else:
                cell = (f"<details><summary title='{_e(x['summary'])}' aria-label='Line {_e(row['line'])}: "
                        f"{_e(row['display'])}. Show how this was worked out'>{_e(row['display'])}</summary>"
                        f"{_explanation_html(x)}</details>")
            out.append(f"<tr><td class='line'>{_e(row['line'])}</td><td>{_e(row['label'])}</td>"
                       f"<td class='amt'>{cell}</td></tr>")
        out.append("</table>")
    out.append("<h2>Review and human decisions</h2>")
    out.append(f"<p>Review / Audit verdict: <b>{_e(form['review']['verdict'])}</b>. {_e(form['review']['summary'])}</p>")
    if form["human_decisions"]:
        out.append("<ul>" + "".join(
            f"<li>{_e(d.get('decided_by'))} chose <b>{_e(d.get('decision'))}</b> for {_e(d.get('escalation_id'))}"
            f"{': ' + _e(d.get('note')) if d.get('note') else ''} ({_e(d.get('decided_at'))})</li>"
            for d in form["human_decisions"]) + "</ul>")
    if form["open_issues"]:
        out.append("<h2>Unresolved issues</h2><ul>" + "".join(f"<li>{_e(i)}</li>" for i in form["open_issues"]) + "</ul>")
    out.append("</body></html>")
    return "\n".join(out)
