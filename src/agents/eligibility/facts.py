"""What Eligibility & Rules decides from: the facts, gathered from shared state.

Facts come from three agents and from the human reviewer:

* Intake's ``intake_record`` (filing status, people, dependents, document inventory), plus the
  extra answers this agent asks Intake to collect (listed in ``QUESTIONS``), which arrive in
  ``intake_answers`` under the same field names;
* Document Extraction's ``extracted_documents`` (W-2s);
* ``entered_documents``: the other forms (1099-R, 1099-INT, 1099-DIV, SSA-1099), entered by hand
  because Extraction reads W-2s only;
* ``human_resolutions``: decisions a person made in the dashboard after an escalation.

Every fact is optional here. A fact the rules need but nobody supplied becomes a question for
Intake, never a default (policy GRD-02).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tax_math.models import FilingStatus

Recipient = Literal["taxpayer", "spouse"]


class PersonFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    ssn: str | None = None
    date_of_birth: date | None = None
    blind: bool | None = None


class DependentFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str | None = None
    relationship: str | None = None
    date_of_birth: date | None = None
    ssn: str | None = None


class InventoryItem(BaseModel):
    """One line of the taxpayer's document list: "I have two W-2s"."""

    model_config = ConfigDict(frozen=True)

    type: str
    count: int = 1


class DocumentFacts(BaseModel):
    """One supplied tax document, reduced to what the rules look at.

    ``boxes`` holds values as entered or extracted, keyed by box label, for example
    ``{"7": "7Y", "2b_taxable_not_determined": False, "ira_sep_simple": True}`` for a 1099-R.
    """

    model_config = ConfigDict(frozen=True)

    document_id: str
    document_type: str
    recipient: Recipient | None = None
    tax_year: int | None = None
    source: Literal["document_extraction", "entered"] = "entered"
    status: str | None = None  # Extraction's status, e.g. "extracted"
    employee_ssn_last4: str | None = None  # W-2 box a, last four digits of a test SSN
    boxes: dict[str, Any] = Field(default_factory=dict)

    def amount(self, box: str) -> Decimal | None:
        raw = self.boxes.get(box)
        if raw in (None, ""):
            return None
        try:
            return Decimal(str(raw).replace(",", "").replace("$", "").strip())
        except InvalidOperation:
            return None

    def has_amount(self, box: str) -> bool:
        value = self.amount(box)
        return value is not None and value != 0


class HumanResolution(BaseModel):
    """A person's decision on an escalation, made in the dashboard.

    ``filing_status`` is set when the human resolved a filing-status escalation by choosing
    the status. ``decision="approved"`` lets a resolvable escalation proceed.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    document_id: str | None = None
    decision: Literal["approved", "rejected"] = "approved"
    filing_status: FilingStatus | None = None
    resolved_by: str
    resolved_at: str
    note: str = ""


class EligibilityFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    filing_status: FilingStatus | None = None
    taxpayer: PersonFacts = PersonFacts()
    spouse: PersonFacts | None = None
    dependents: list[DependentFacts] = []
    lived_apart_from_spouse_all_year: bool | None = None

    # Asked on this agent's behalf (see QUESTIONS).
    spouse_died_in_2025: bool | None = None
    spouse_death_year: int | None = None
    remarried_before_end_of_2025: bool | None = None
    can_be_claimed_as_dependent: bool | None = None
    spouse_can_be_claimed_as_dependent: bool | None = None
    dual_status_alien: bool | None = None
    spouse_itemizes: bool | None = None
    spouse_had_income: bool | None = None
    spouse_filing_return: bool | None = None
    paid_over_half_home_cost: bool | None = None
    child_lived_with_you_all_year: bool | None = None

    document_inventory: list[InventoryItem] = []
    documents: list[DocumentFacts] = []
    documents_supplied: bool = False  # the taxpayer has handed over their documents
    schedules_entered: list[str] = []  # schedules the taxpayer entered amounts on: "1", "2", "3"
    computed_lines: dict[str, int] = {}  # from Computation, e.g. {"2b": 1200, "3b": 300}
    human_resolutions: list[HumanResolution] = []


# Plain-language questions for Intake, one fact each. Intake owns the wording it actually
# uses; these say what is needed. Keys are the field names Intake should answer under.
QUESTIONS: dict[str, str] = {
    "filing_status": "Which filing status are you using this year?",
    "taxpayer_ssn": "What is your Social Security number?",
    "taxpayer_dob": "What is your date of birth?",
    "taxpayer_blind": "Were you legally blind at the end of 2025?",
    "spouse_ssn": "What is your husband's or wife's Social Security number?",
    "spouse_dob": "What is your husband's or wife's date of birth?",
    "spouse_blind": "Was your husband or wife legally blind at the end of 2025?",
    "lived_apart_all_year": "Did you and your husband or wife live apart for all of 2025?",
    "spouse_died_in_2025": "Did your husband or wife pass away during 2025?",
    "spouse_death_year": "In what year did your husband or wife pass away?",
    "remarried_before_end_of_2025": "Did you remarry before the end of 2025?",
    "can_be_claimed_as_dependent": "Can anyone else claim you as a dependent on their tax return?",
    "spouse_can_be_claimed_as_dependent": (
        "Can anyone else claim your husband or wife as a dependent on their tax return?"
    ),
    "dual_status_alien": (
        "During 2025, were you a U.S. citizen or resident for only part of the year?"
    ),
    "spouse_itemizes": "Is your husband or wife itemizing deductions on their own return?",
    "spouse_had_income": "Did your husband or wife have any income in 2025?",
    "spouse_filing_return": "Is your husband or wife filing their own tax return for 2025?",
    "paid_over_half_home_cost": "Did you pay more than half the cost of keeping up your home in 2025?",
    "child_lived_with_you_all_year": "Did your child live with you for all of 2025?",
}

_FACT_FIELDS = set(EligibilityFacts.model_fields) - {
    "filing_status", "taxpayer", "spouse", "dependents", "document_inventory", "documents",
    "documents_supplied", "schedules_entered", "computed_lines", "human_resolutions",
}


def _person(raw: dict | None) -> PersonFacts | None:
    if not raw:
        return None
    blind = raw.get("blind", raw.get("legally_blind"))
    return PersonFacts(ssn=raw.get("ssn"), date_of_birth=raw.get("date_of_birth"), blind=blind)


def _from_intake_record(record: dict) -> dict[str, Any]:
    """Map Intake's IntakeRecord (src/agents/intake.py) onto facts."""
    out: dict[str, Any] = {
        "filing_status": record.get("filing_status"),
        "taxpayer": _person(record.get("taxpayer")) or PersonFacts(),
        "spouse": _person(record.get("spouse")),
        "dependents": [DependentFacts(**d) for d in record.get("dependents") or []],
        "lived_apart_from_spouse_all_year": record.get("lived_apart_from_spouse_all_year"),
        "document_inventory": [
            InventoryItem(type=d["type"], count=d.get("count", 1)) for d in record.get("documents") or []
        ],
    }
    return {k: v for k, v in out.items() if v is not None}


def _from_extraction(result: dict) -> DocumentFacts:
    identifiers = result.get("identifiers") or {}
    masked = identifiers.get("employee_ssn_masked") or ""
    last4 = masked[-4:] if identifiers.get("ssn_check") == "valid_test" and masked[-4:].isdigit() else None
    year = identifiers.get("tax_year")
    return DocumentFacts(
        document_id=result["document_id"],
        document_type=result.get("document_type") or "unknown",
        source="document_extraction",
        status=result.get("status"),
        tax_year=int(year) if year and str(year).isdigit() else None,
        employee_ssn_last4=last4,
        boxes={box: field.get("value") for box, field in (result.get("fields") or {}).items()},
    )


def facts_from_state(state: dict[str, Any]) -> EligibilityFacts:
    """Gather facts from shared state. Later sources override earlier ones:
    intake_record, then intake_answers, then eligibility_facts (a direct override for tests
    and demos)."""
    data: dict[str, Any] = {}
    if record := state.get("intake_record"):
        data.update(_from_intake_record(record))
    answers = state.get("intake_answers") or {}
    data.update({k: v for k, v in answers.items() if k in _FACT_FIELDS and v is not None})

    documents: dict[str, DocumentFacts] = {}
    for result in state.get("extracted_documents") or []:
        documents[result["document_id"]] = _from_extraction(result)
    for entered in state.get("entered_documents") or []:
        documents[entered["document_id"]] = DocumentFacts(**entered)  # hand entry wins
    if documents:
        data["documents"] = [documents[k] for k in sorted(documents)]
        data["documents_supplied"] = True
    if state.get("computed_lines"):
        data["computed_lines"] = state["computed_lines"]
    if state.get("human_resolutions"):
        data["human_resolutions"] = state["human_resolutions"]
    if state.get("schedules_entered"):
        data["schedules_entered"] = state["schedules_entered"]

    data.update(state.get("eligibility_facts") or {})
    return EligibilityFacts.model_validate(data)
