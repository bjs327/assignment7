"""Typed records produced by the Document Extraction agent.

Two layers:

* ``W2Reading`` is what the vision model is asked to *transcribe*: strings
  exactly as printed. The model never computes, rounds or interprets.
* ``ExtractionResult`` is the validated, typed record the rest of SilverFile
  consumes. Every amount carries a ``Provenance`` record (spec section 4.2).

State stores plain dicts (``model_dump(mode="json")``) so LangGraph
checkpointers can serialize it without custom types.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# What the model transcribes
# --------------------------------------------------------------------------


class Box12Entry(BaseModel):
    code: str = Field(description="The letter code printed in box 12, e.g. 'D' or 'DD'.")
    amount: str = Field(description="The amount exactly as printed next to the code.")


class W2Reading(BaseModel):
    """A literal transcription of one Form W-2. Copy what is printed; never calculate."""

    tax_year: str | None = Field(None, description="The tax year printed on the form, e.g. '2025'.")
    employee_ssn: str | None = Field(None, description="Box a, exactly as printed.")
    employer_ein: str | None = Field(None, description="Box b, exactly as printed.")
    employer_name: str | None = Field(None, description="Box c, the employer's name only (first line).")
    employee_name: str | None = Field(None, description="Box e, the employee's name.")
    box1_wages: str | None = Field(None, description="Box 1 amount exactly as printed, or null if blank.")
    box2_federal_withheld: str | None = Field(None, description="Box 2 amount exactly as printed, or null if blank.")
    box3_ss_wages: str | None = Field(None, description="Box 3 amount exactly as printed, or null if blank.")
    box4_ss_withheld: str | None = Field(None, description="Box 4 amount exactly as printed, or null if blank.")
    box5_medicare_wages: str | None = Field(None, description="Box 5 amount exactly as printed, or null if blank.")
    box6_medicare_withheld: str | None = Field(None, description="Box 6 amount exactly as printed, or null if blank.")
    box12: list[Box12Entry] = Field(default_factory=list, description="Each box 12 entry that is filled in.")
    unreadable_boxes: list[str] = Field(
        default_factory=list,
        description="Labels of boxes you could not read confidently, e.g. ['1', '2']. Leave those values null.",
    )


# --------------------------------------------------------------------------
# What the rest of the system consumes
# --------------------------------------------------------------------------

IssueSeverity = Literal["info", "needs_confirmation", "escalate"]
ResultStatus = Literal["extracted", "needs_review", "failed", "manual_entry_required"]


class Provenance(BaseModel):
    """Where a value came from. Shown on hover/click in the draft form."""

    source_type: Literal["document_extraction"] = "document_extraction"
    document_id: str
    document_type: str
    file_name: str
    file_sha256: str
    location: str  # e.g. "W-2 box 1"
    raw_text: str  # exactly what the model read, before parsing
    method: str  # e.g. "vision-llm:claude-sonnet-5-5"
    extracted_at: str  # ISO 8601 UTC
    attempt: int = 1


class ExtractedAmount(BaseModel):
    box: str
    label: str
    value: str  # Decimal as a string, two places, e.g. "18500.00"
    # Ready for tax_math's LabeledAmount, e.g. "W-2 #1 (Acme Corp) box 1"
    source_label: str
    provenance: Provenance


class ExtractionIssue(BaseModel):
    code: str
    severity: IssueSeverity
    message: str  # plain language; never contains an unmasked SSN
    box: str | None = None


class DocumentIdentifiers(BaseModel):
    employer_name: str | None = None
    employer_ein: str | None = None
    employee_name: str | None = None
    employee_ssn_masked: str | None = None  # "XXX-XX-1234" or "[SSN REDACTED]"
    ssn_check: str = "missing"  # result of security.check_ssn
    tax_year: str | None = None


class ExtractionResult(BaseModel):
    document_id: str
    document_type: str
    status: ResultStatus
    attempt: int = 1
    file_name: str | None = None
    file_sha256: str | None = None
    fields: dict[str, ExtractedAmount] = Field(default_factory=dict)
    box12: list[dict] = Field(default_factory=list)
    identifiers: DocumentIdentifiers = Field(default_factory=DocumentIdentifiers)
    issues: list[ExtractionIssue] = Field(default_factory=list)
    extracted_at: str | None = None
