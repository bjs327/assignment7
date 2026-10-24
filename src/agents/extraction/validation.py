"""Deterministic checks on a W-2 transcription.

This turns the model's literal strings into typed amounts and raises issues.
It never decides what a value *means for the return* (that is the Rules and
Computation agents' job) and never "fixes" a value: anything doubtful is
flagged for a human instead.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from security import check_ssn, mask_ssn

from .schemas import (
    DocumentIdentifiers,
    ExtractedAmount,
    ExtractionIssue,
    ExtractionResult,
    Provenance,
    W2Reading,
)

TARGET_TAX_YEAR = "2025"

# (reading attribute, box label, human label)
W2_AMOUNT_BOXES: list[tuple[str, str, str]] = [
    ("box1_wages", "1", "Wages, tips, other compensation"),
    ("box2_federal_withheld", "2", "Federal income tax withheld"),
    ("box3_ss_wages", "3", "Social security wages"),
    ("box4_ss_withheld", "4", "Social security tax withheld"),
    ("box5_medicare_wages", "5", "Medicare wages and tips"),
    ("box6_medicare_withheld", "6", "Medicare tax withheld"),
]

_CENTS = Decimal("0.01")


def parse_amount(raw: str | None) -> tuple[Decimal | None, str | None]:
    """Parse a printed dollar amount. Returns (value, error_code).

    Accepts "$18,500.00", "18500", "18 500.00". Rejects negatives, parentheses,
    letters and more than two decimal places rather than guessing.
    """
    if raw is None or not raw.strip():
        return None, "blank"
    text = raw.strip()
    if text.startswith("-") or (text.startswith("(") and text.endswith(")")):
        return None, "negative"
    cleaned = re.sub(r"[\s$,]", "", text)
    if not re.fullmatch(r"\d+(\.\d{1,2})?", cleaned):
        return None, "not_a_number"
    try:
        return Decimal(cleaned).quantize(_CENTS), None
    except InvalidOperation:
        return None, "not_a_number"


def build_w2_result(
    *,
    document: dict,
    reading: W2Reading,
    file_name: str,
    file_sha256: str,
    method: str,
    extracted_at: str,
    attempt: int,
) -> ExtractionResult:
    doc_id = document["document_id"]
    employer = (reading.employer_name or "employer unreadable").strip()
    issues: list[ExtractionIssue] = []
    fields: dict[str, ExtractedAmount] = {}
    unreadable = {b.strip().lower().removeprefix("box").strip() for b in reading.unreadable_boxes}

    def provenance(box: str, raw: str) -> Provenance:
        return Provenance(
            document_id=doc_id,
            document_type="W-2",
            file_name=file_name,
            file_sha256=file_sha256,
            location=f"W-2 box {box}",
            raw_text=raw,
            method=method,
            extracted_at=extracted_at,
            attempt=attempt,
        )

    # ---- amounts --------------------------------------------------------
    for attr, box, label in W2_AMOUNT_BOXES:
        raw = getattr(reading, attr)
        if box in unreadable:
            issues.append(ExtractionIssue(
                code="unreadable_box", severity="escalate", box=box,
                message=f"I couldn't read box {box} ({label}) clearly. A person should check the original.",
            ))
            continue
        value, error = parse_amount(raw)
        if error == "blank":
            if box == "1":
                issues.append(ExtractionIssue(
                    code="missing_wages", severity="escalate", box=box,
                    message="Box 1 (wages) appears blank. A person should check the original.",
                ))
            elif box == "2":
                issues.append(ExtractionIssue(
                    code="no_federal_withholding", severity="needs_confirmation", box=box,
                    message="Box 2 (federal tax withheld) appears blank. Please confirm no tax was withheld.",
                ))
            continue
        if error:
            issues.append(ExtractionIssue(
                code=f"amount_{error}", severity="escalate", box=box,
                message=f"Box {box} ({label}) doesn't look like a normal dollar amount. A person should check it.",
            ))
            continue
        fields[f"box{box}"] = ExtractedAmount(
            box=box, label=label, value=str(value),
            source_label=f"W-2 {doc_id} ({employer}) box {box}",
            provenance=provenance(box, raw),
        )

    box12 = []
    for entry in reading.box12:
        value, error = parse_amount(entry.amount)
        if error:
            issues.append(ExtractionIssue(
                code=f"box12_amount_{error}", severity="needs_confirmation", box="12",
                message=f"A box 12 amount (code {entry.code}) couldn't be read as a number.",
            ))
            continue
        box12.append({"code": entry.code.strip().upper(), "value": str(value),
                      "provenance": provenance("12", entry.amount).model_dump(mode="json")})

    # ---- sanity checks (flag only, never correct) ----------------------
    if "box1" in fields and "box2" in fields:
        if Decimal(fields["box2"].value) > Decimal(fields["box1"].value):
            issues.append(ExtractionIssue(
                code="withholding_exceeds_wages", severity="needs_confirmation", box="2",
                message="Federal tax withheld (box 2) is larger than wages (box 1). Please double-check the form.",
            ))

    # ---- tax year: prior-year returns are out of scope -----------------
    year = (reading.tax_year or "").strip() or None
    if year is None:
        issues.append(ExtractionIssue(
            code="tax_year_missing", severity="needs_confirmation",
            message="I couldn't find the tax year on this W-2. Please confirm it is for 2025.",
        ))
    elif year != TARGET_TAX_YEAR:
        issues.append(ExtractionIssue(
            code="wrong_tax_year", severity="escalate",
            message=f"This W-2 appears to be for {year}, not {TARGET_TAX_YEAR}. Prior-year returns need a person.",
        ))

    # ---- SSN (Security): only 900-999 test SSNs are ever accepted -------
    ssn_status = check_ssn(reading.employee_ssn)
    if ssn_status == "not_test_range":
        issues.append(ExtractionIssue(
            code="non_test_ssn", severity="escalate", box="a",
            message=("This W-2 shows a Social Security number outside the test range (900-999). "
                     "It was not saved. Please use a sample document with a test number."),
        ))
    elif ssn_status in ("missing", "malformed"):
        issues.append(ExtractionIssue(
            code=f"ssn_{ssn_status}", severity="needs_confirmation", box="a",
            message="I couldn't read the Social Security number on this W-2.",
        ))

    identifiers = DocumentIdentifiers(
        employer_name=reading.employer_name,
        employer_ein=reading.employer_ein,
        employee_name=reading.employee_name,
        employee_ssn_masked=mask_ssn(reading.employee_ssn) if reading.employee_ssn else None,
        ssn_check=ssn_status,
        tax_year=year,
    )

    status = "needs_review" if any(i.severity == "escalate" for i in issues) else "extracted"
    return ExtractionResult(
        document_id=doc_id,
        document_type="W-2",
        status=status,
        attempt=attempt,
        file_name=file_name,
        file_sha256=file_sha256,
        fields=fields,
        box12=box12,
        identifiers=identifiers,
        issues=issues,
        extracted_at=extracted_at,
    )
