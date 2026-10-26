"""The decisions Eligibility & Rules makes, as plain deterministic Python.

``evaluate(facts)`` returns an ``EligibilityDecision``. Every decision in it cites rule IDs
from data/policy.md (see ``corpus.py``), so the audit record can show the rule behind each
outcome. There is no model call here: the same facts always give the same decision, and the
decision table is unit-tested (tests/eligibility/).

Three outcomes:

* ``complete``: every check passed. ``computation_inputs`` is ready for the tax_math tools.
* ``needs_facts``: a fact the rules need is missing. ``missing_facts`` lists the questions for
  Intake. Nothing is assumed (GRD-02).
* ``escalated``: something needs a human (ESC-01 to ESC-05). ``escalations`` says what and why.
  This agent never calls ``interrupt()`` itself; the Orchestrator does.

Comparing dates and checking whether a box has an amount is rule application, not
arithmetic: no dollar amount is computed here.
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from security import check_ssn
from tax_math.models import FilingStatus

from .corpus import citation_records
from .facts import QUESTIONS, DocumentFacts, EligibilityFacts, HumanResolution

TAX_YEAR = 2025
BORN_BEFORE = date(1961, 1, 2)  # ELIG-01, ELIG-02, ELIG-03
YEAR_END = date(TAX_YEAR, 12, 31)
SCHEDULE_B_THRESHOLD = 1500  # SCH-01

FS = FilingStatus
MARRIED = {FS.MARRIED_FILING_JOINTLY, FS.MARRIED_FILING_SEPARATELY}

IN_SCOPE_TYPES = {"W-2", "SSA-1099", "1099-R", "1099-INT", "1099-DIV"}
# Known out-of-scope documents and the SCOPE-02 item each one brings in.
OUT_OF_SCOPE_TYPES = {
    "1099-NEC": "nonemployee compensation (self-employment, Schedule C)",
    "1099-K": "payment card or app payments (business income, Schedule C)",
    "1099-MISC": "miscellaneous income (business or rental income, Schedules C and E)",
    "1099-B": "sales of stocks or other assets (capital gains, Schedule D)",
    "1099-S": "real estate sale (capital gains, Schedule D)",
    "1099-DA": "digital asset transactions",
    "K-1": "partnership, S corporation or trust income (Schedule E)",
    "SCHEDULE K-1": "partnership, S corporation or trust income (Schedule E)",
    "1098": "mortgage interest (itemized deductions, Schedule A)",
    "SSA-1042S": "Social Security benefits for a nonresident alien (foreign status)",
    "1042-S": "foreign person's U.S. income",
}
# SCOPE-04: box 7 code combinations that are in scope.
IN_SCOPE_1099R_CODES = {frozenset("7"), frozenset("7Y")}
QSS_CHILD_RELATIONSHIPS = {
    "child", "son", "daughter", "stepchild", "stepson", "stepdaughter",
    "adopted child", "adopted son", "adopted daughter",
}

EscalationCategory = Literal[
    "out_of_scope",
    "user_document_conflict",
    "taxable_amount_not_determined",
    "filing_status_ambiguity",
    "security",
]
# Escalations a human may approve so the return can continue. Out-of-scope items, the
# 1099-R box 2b case and non-test SSNs never become in scope by approval.
RESOLVABLE_CODES = {
    "spouse_died_in_tax_year",
    "filing_status_unsupported",
    "w2_ssn_mismatch",
    "document_recipient_conflict",
    "document_count_mismatch",
    "qcd_age_conflict",
}


# --- Output ------------------------------------------------------------------------------


class Finding(BaseModel):
    """One decision and what it rests on. Shown in the audit record."""

    topic: str  # e.g. "filing_status", "line_12d.taxpayer_65_or_older", "document.w2-1"
    value: Any
    reason: str
    rule_ids: list[str]
    decided_by: Literal["rules", "human"] = "rules"
    citations: list[dict[str, str]] = Field(default_factory=list)


class MissingFact(BaseModel):
    field: str
    question: str
    why: str
    rule_ids: list[str]


class EligibilityEscalation(BaseModel):
    code: str
    category: EscalationCategory
    reason: str
    rule_ids: list[str]
    document_id: str | None = None
    resolvable: bool = False
    citations: list[dict[str, str]] = Field(default_factory=list)


class DocumentRouting(BaseModel):
    document_id: str
    document_type: str
    in_scope: bool
    form_lines: list[str] = Field(default_factory=list)  # e.g. ["4a", "4b"] for an IRA 1099-R
    qcd: bool = False
    note: str = ""


class ScheduleDecision(BaseModel):
    schedule: str
    applies: bool | None  # None: depends on lines Computation hasn't produced yet
    reason: str
    rule_ids: list[str]


class EligibilityDecision(BaseModel):
    status: Literal["complete", "needs_facts", "escalated"]
    tax_year: int = TAX_YEAR
    filing_status: FilingStatus | None = None
    form_1040sr_eligible: bool | None = None
    line_12d: dict[str, bool] | None = None
    senior_deduction: dict[str, bool] | None = None
    computation_inputs: dict[str, Any] | None = None
    documents: list[DocumentRouting] = Field(default_factory=list)
    schedules: list[ScheduleDecision] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    missing_facts: list[MissingFact] = Field(default_factory=list)
    escalations: list[EligibilityEscalation] = Field(default_factory=list)


# --- Helpers -----------------------------------------------------------------------------


def born_before_cutoff(dob: date) -> bool:
    """ELIG-01/02/03: born before January 2, 1961, i.e. 65 or older by the end of 2025."""
    return dob < BORN_BEFORE


def reached_70_and_a_half_by(dob: date, on: date) -> bool:
    """SCOPE-05: whether someone born on ``dob`` is 70½ on ``on``. Calendar months only."""
    year, month = dob.year + 70 + (dob.month + 5) // 12, (dob.month + 5) % 12 + 1
    day = min(dob.day, calendar.monthrange(year, month)[1])
    return date(year, month, day) <= on


def parse_distribution_codes(raw: Any) -> frozenset[str]:
    """1099-R box 7 codes are single characters: "7Y", "7, Y" and "Y7" all mean {7, Y}."""
    return frozenset(c for c in str(raw or "").upper() if c.isalnum())


def normalize_type(raw: str) -> str:
    text = raw.strip().upper().replace("_", "-").replace(" ", "")
    aliases = {"W2": "W-2", "SSA1099": "SSA-1099", "1099R": "1099-R", "1099INT": "1099-INT",
               "1099DIV": "1099-DIV", "1099NEC": "1099-NEC", "1099MISC": "1099-MISC",
               "1099B": "1099-B", "1099K": "1099-K", "1099S": "1099-S", "1099DA": "1099-DA",
               "SCHEDULEK-1": "K-1", "K1": "K-1", "SSA-1042-S": "SSA-1042S"}
    return aliases.get(text, text)


class _Run:
    """Collects findings, questions and escalations while the checks run."""

    def __init__(self, facts: EligibilityFacts) -> None:
        self.facts = facts
        self.findings: list[Finding] = []
        self.missing: dict[str, MissingFact] = {}
        self.escalations: list[EligibilityEscalation] = []

    def find(self, topic: str, value: Any, reason: str, rule_ids: list[str], decided_by="rules") -> Any:
        self.findings.append(
            Finding(topic=topic, value=value, reason=reason, rule_ids=rule_ids,
                    decided_by=decided_by, citations=citation_records(rule_ids))
        )
        return value

    def need(self, field: str, why: str, rule_ids: list[str]) -> None:
        if field not in self.missing:
            question = QUESTIONS.get(field) or f"Please provide: {field.replace('.', ' ').replace('_', ' ')}."
            self.missing[field] = MissingFact(field=field, question=question, why=why, rule_ids=rule_ids)

    def resolution(self, code: str, document_id: str | None = None) -> HumanResolution | None:
        for r in reversed(self.facts.human_resolutions):  # the latest decision wins
            if r.code == code and r.document_id == document_id:
                return r
        return None

    def escalate(self, code: str, category: EscalationCategory, reason: str, rule_ids: list[str],
                 document_id: str | None = None) -> bool:
        """Raise an escalation unless a human already approved it. True if it was raised."""
        resolvable = code in RESOLVABLE_CODES
        resolution = self.resolution(code, document_id) if resolvable else None
        if resolution and resolution.decision == "approved":
            self.find(
                f"escalation.{code}" + (f".{document_id}" if document_id else ""),
                "approved by a human",
                f"{reason} Approved by {resolution.resolved_by} at {resolution.resolved_at}"
                + (f": {resolution.note}" if resolution.note else "."),
                rule_ids, decided_by="human",
            )
            return False
        if any(e.code == code and e.document_id == document_id for e in self.escalations):
            return True
        self.escalations.append(
            EligibilityEscalation(code=code, category=category, reason=reason, rule_ids=rule_ids,
                                  document_id=document_id, resolvable=resolvable,
                                  citations=citation_records(rule_ids))
        )
        return True


# --- The checks --------------------------------------------------------------------------


def _check_ssns(run: _Run, status: FilingStatus | None) -> None:
    """GRD-03: every SSN must be in the 900-999 test range. The number is never repeated."""
    f = run.facts
    people = [("taxpayer", f.taxpayer.ssn)]
    if status in MARRIED or f.spouse is not None:
        people.append(("spouse", f.spouse.ssn if f.spouse else None))
    people += [(f"dependent.{n}", d.ssn) for n, d in enumerate(f.dependents, start=1)]
    for who, ssn in people:
        status = check_ssn(ssn)
        if status == "not_test_range":
            run.escalate(f"non_test_ssn.{who}", "security",
                         f"The SSN given for the {who.replace('.', ' ')} is outside the 900-999 test "
                         "range, so it may be a real SSN. It was not stored.", ["GRD-03"])
        elif status in ("missing", "malformed"):
            field = f"{who}.ssn" if who.startswith("dependent") else f"{who}_ssn"
            run.need(field, f"The return needs a test SSN for the {who.replace('.', ' ')}.", ["GRD-03"])


def _check_filing_status(run: _Run) -> FilingStatus | None:
    """FS-01 to FS-06. Returns the status the rest of the checks should use, or None."""
    f = run.facts
    resolved = run.resolution("spouse_died_in_tax_year") or run.resolution("filing_status_unsupported")
    if resolved and resolved.decision == "approved" and resolved.filing_status is not None:
        return run.find(
            "filing_status", resolved.filing_status,
            f"Confirmed by {resolved.resolved_by} at {resolved.resolved_at} after escalation"
            + (f": {resolved.note}" if resolved.note else "."),
            ["FS-06", "ESC-05"], decided_by="human",
        )

    status = f.filing_status
    if status is None:
        run.need("filing_status", "Every check depends on the filing status.", ["SCOPE-01"])
        return None

    # FS-06: a spouse who died in 2025 always goes to a human, whatever status was chosen.
    if status is FS.QUALIFYING_SURVIVING_SPOUSE:
        if f.spouse_death_year is None:
            run.need("spouse_death_year", "Qualifying surviving spouse depends on the year the spouse died.", ["FS-05"])
        elif f.spouse_death_year == TAX_YEAR and run.escalate("spouse_died_in_tax_year", "filing_status_ambiguity",
                         "The spouse died in 2025, which rules out qualifying surviving spouse for 2025. "
                         "Married filing jointly or single may apply: a person needs to decide.",
                         ["FS-05", "FS-06", "ESC-05"]):
            return None
    elif f.spouse_died_in_2025 is None:
        run.need("spouse_died_in_2025", "A spouse who died during the year changes which status applies.", ["FS-06"])
    elif f.spouse_died_in_2025 and run.escalate("spouse_died_in_tax_year", "filing_status_ambiguity",
                     f"The taxpayer chose {status.label.lower()}, and their spouse died during 2025. "
                     "Married filing jointly, single or another status may apply: a person needs to decide.",
                     ["FS-02", "FS-06", "ESC-05"]):
        return None

    problems: list[str] = []
    rule = {FS.SINGLE: "FS-01", FS.MARRIED_FILING_JOINTLY: "FS-02", FS.MARRIED_FILING_SEPARATELY: "FS-03",
            FS.HEAD_OF_HOUSEHOLD: "FS-04", FS.QUALIFYING_SURVIVING_SPOUSE: "FS-05"}[status]

    if status is FS.MARRIED_FILING_SEPARATELY and f.lived_apart_from_spouse_all_year is None:
        run.need("lived_apart_all_year", "Line 6d and the Social Security base amount depend on it.", ["FS-03", "SSA-02"])
    if status is FS.HEAD_OF_HOUSEHOLD:
        if f.paid_over_half_home_cost is None:
            run.need("paid_over_half_home_cost", "Head of household requires paying over half the cost of the home.", ["FS-04"])
        elif not f.paid_over_half_home_cost:
            problems.append("the taxpayer did not pay over half the cost of keeping up the home")
        if not f.dependents:
            problems.append("no dependent is listed as the qualifying person")
    if status is FS.QUALIFYING_SURVIVING_SPOUSE:
        if f.spouse_death_year is not None and f.spouse_death_year not in (TAX_YEAR - 2, TAX_YEAR - 1):
            problems.append(f"the spouse died in {f.spouse_death_year}, not 2023 or 2024")
        if f.remarried_before_end_of_2025 is None:
            run.need("remarried_before_end_of_2025", "Remarrying ends qualifying surviving spouse status.", ["FS-05"])
        elif f.remarried_before_end_of_2025:
            problems.append("the taxpayer remarried before the end of 2025")
        children = [d for d in f.dependents if (d.relationship or "").strip().lower() in QSS_CHILD_RELATIONSHIPS]
        if not children:
            problems.append("no child or stepchild is listed as a dependent")
        if f.child_lived_with_you_all_year is None:
            run.need("child_lived_with_you_all_year", "The child must have lived in the home all year.", ["FS-05"])
        elif not f.child_lived_with_you_all_year:
            problems.append("the child did not live in the home all year")
        if f.paid_over_half_home_cost is None:
            run.need("paid_over_half_home_cost", "Qualifying surviving spouse requires paying over half the cost of the home.", ["FS-05"])
        elif not f.paid_over_half_home_cost:
            problems.append("the taxpayer did not pay over half the cost of keeping up the home")

    if problems and run.escalate("filing_status_unsupported", "filing_status_ambiguity",
                     f"The answers don't support {status.label.lower()}: " + "; ".join(problems) + ".",
                     [rule, "ESC-05"]):
        return None
    return run.find("filing_status", status, f"{status.label}: the answers given support this status.", [rule])


def _check_chart_applies(run: _Run, status: FilingStatus | None) -> None:
    """ELIG-04: the three cases where the Standard Deduction Chart doesn't apply."""
    f = run.facts
    checks = [("can_be_claimed_as_dependent", f.can_be_claimed_as_dependent,
               "Someone can claim the taxpayer as a dependent, so the Standard Deduction Worksheet for "
               "Dependents applies instead of the chart."),
              ("dual_status_alien", f.dual_status_alien,
               "The taxpayer was a dual-status alien, so the standard deduction is zero.")]
    if status is FS.MARRIED_FILING_JOINTLY:
        checks.append(("spouse_can_be_claimed_as_dependent", f.spouse_can_be_claimed_as_dependent,
                       "Someone can claim the spouse as a dependent, so the Standard Deduction Worksheet for "
                       "Dependents applies instead of the chart."))
    if status is FS.MARRIED_FILING_SEPARATELY:
        checks.append(("spouse_itemizes", f.spouse_itemizes,
                       "The spouse itemizes on a separate return, so the standard deduction is zero."))
    for field, answer, reason in checks:
        if answer is None:
            run.need(field, "Decides whether the Standard Deduction Chart applies.", ["ELIG-04"])
        elif answer:
            run.escalate(f"chart_not_applicable.{field}", "out_of_scope", reason, ["ELIG-04", "ESC-01"])


def _check_1040sr_and_boxes(run: _Run, status: FilingStatus | None) -> tuple[dict | None, dict | None]:
    """ELIG-01 (1040-SR), ELIG-02 (line 12d boxes) and ELIG-03 (senior deduction).

    ``status`` is the confirmed filing status, or None while it is missing or escalated. The
    1040-SR check still runs against the status the taxpayer chose; the boxes wait.
    """
    f = run.facts
    claimed = status or f.filing_status
    tp, sp = f.taxpayer, f.spouse
    if tp.date_of_birth is None:
        run.need("taxpayer_dob", "Form 1040-SR eligibility and the age box depend on it.", ["ELIG-01", "ELIG-02"])
    if tp.blind is None:
        run.need("taxpayer_blind", "The blindness box on line 12d depends on it.", ["ELIG-02"])
    joint = claimed is FS.MARRIED_FILING_JOINTLY
    if claimed in MARRIED and sp is None:
        rule = "FS-02" if joint else "FS-03"
        run.need("spouse_dob", "A married filing status needs the spouse's details.", [rule, "ELIG-02"])
        run.need("spouse_blind", "A married filing status needs the spouse's details.", [rule, "ELIG-02"])
    if claimed in MARRIED and sp is not None:
        if sp.date_of_birth is None:
            run.need("spouse_dob", "The spouse's age box depends on it.", ["ELIG-02"])
        if sp.blind is None:
            run.need("spouse_blind", "The spouse's blindness box depends on it.", ["ELIG-02"])

    # ELIG-01. On a joint return, either spouse's age is enough.
    tp_old = born_before_cutoff(tp.date_of_birth) if tp.date_of_birth else None
    sp_old = born_before_cutoff(sp.date_of_birth) if (sp and sp.date_of_birth) else None
    if tp_old:
        run.find("form_1040sr_eligible", True, "The taxpayer was born before January 2, 1961.", ["ELIG-01"])
    elif joint and sp_old:
        run.find("form_1040sr_eligible", True,
                 "The spouse was born before January 2, 1961, which is enough on a joint return.", ["ELIG-01"])
    elif tp_old is False and (not joint or sp_old is False):
        run.find("form_1040sr_eligible", False, "No one who counts was born before January 2, 1961.", ["ELIG-01"])
        run.escalate("not_1040sr_eligible", "out_of_scope",
                     "Neither the taxpayer" + (" nor the spouse" if joint else "")
                     + " was born before January 2, 1961, so the return is Form 1040, not 1040-SR.",
                     ["ELIG-01", "ESC-01"])

    if status is None or tp_old is None or tp.blind is None:
        return None, None
    joint = status is FS.MARRIED_FILING_JOINTLY
    if status in MARRIED and (sp is None or sp_old is None or sp.blind is None):
        return None, None

    # ELIG-02: the four boxes.
    boxes = {
        "taxpayer_65_or_older": run.find("line_12d.taxpayer_65_or_older", tp_old,
                                         f"Born {tp.date_of_birth.isoformat()}: "
                                         + ("before" if tp_old else "on or after") + " January 2, 1961.", ["ELIG-02"]),
        "taxpayer_blind": run.find("line_12d.taxpayer_blind", tp.blind,
                                   "Blind at the end of 2025." if tp.blind else "Not blind at the end of 2025.", ["ELIG-02"]),
        "spouse_65_or_older": False,
        "spouse_blind": False,
    }
    if joint:
        boxes["spouse_65_or_older"] = run.find("line_12d.spouse_65_or_older", sp_old,
                                               f"Spouse born {sp.date_of_birth.isoformat()}.", ["ELIG-02"])
        boxes["spouse_blind"] = run.find("line_12d.spouse_blind", sp.blind, "Joint return: spouse boxes apply.", ["ELIG-02"])
    elif status is FS.MARRIED_FILING_SEPARATELY and (sp_old or sp.blind):
        # Spouse boxes on a separate return only if the spouse had no income, isn't filing
        # and can't be claimed as a dependent.
        conditions = {"spouse_had_income": f.spouse_had_income, "spouse_filing_return": f.spouse_filing_return,
                      "spouse_can_be_claimed_as_dependent": f.spouse_can_be_claimed_as_dependent}
        for field, answer in conditions.items():
            if answer is None:
                run.need(field, "Decides whether the spouse boxes on line 12d can be checked on a separate return.", ["ELIG-02"])
        if any(v is None for v in conditions.values()):
            return None, None
        allowed = not any(conditions.values())
        boxes["spouse_65_or_older"] = run.find(
            "line_12d.spouse_65_or_older", bool(sp_old and allowed),
            "Separate return: spouse had no income, isn't filing and can't be claimed as a dependent."
            if allowed else "Separate return: spouse boxes don't apply because the spouse had income, is "
            "filing, or can be claimed as a dependent.", ["ELIG-02"])
        boxes["spouse_blind"] = run.find("line_12d.spouse_blind", bool(sp.blind and allowed),
                                         "Same conditions as the spouse age box.", ["ELIG-02"])
    else:
        run.find("line_12d.spouse_boxes", False, f"{status.label}: the spouse boxes never apply.", ["ELIG-02"])

    # ELIG-03: senior deduction. Married filing separately gets none.
    tp_ssn_ok = check_ssn(tp.ssn) == "valid_test"
    sp_ssn_ok = bool(sp) and check_ssn(sp.ssn) == "valid_test"
    if status is FS.MARRIED_FILING_SEPARATELY:
        senior = {"taxpayer_qualifies": run.find("senior_deduction.taxpayer_qualifies", False,
                                                 "Married filing separately can't take the senior deduction.",
                                                 ["ELIG-03", "FS-03"]),
                  "spouse_qualifies": False}
    else:
        senior = {
            "taxpayer_qualifies": run.find("senior_deduction.taxpayer_qualifies", bool(tp_old and tp_ssn_ok),
                                           "Born before January 2, 1961 with a valid SSN." if tp_old and tp_ssn_ok
                                           else "Not 65 or older by the end of 2025, or no valid SSN.", ["ELIG-03"]),
            "spouse_qualifies": run.find("senior_deduction.spouse_qualifies", bool(joint and sp_old and sp_ssn_ok),
                                         "Joint return; spouse born before January 2, 1961 with a valid SSN."
                                         if joint and sp_old and sp_ssn_ok else
                                         "The spouse amount needs a joint return and a spouse 65 or older.", ["ELIG-03"]),
        }
    return boxes, senior


def _check_documents(run: _Run, status: FilingStatus | None) -> list[DocumentRouting]:
    """SCOPE-01 to SCOPE-05, ESC-02 and ESC-03, document by document."""
    f = run.facts
    routing: list[DocumentRouting] = []
    joint = status is FS.MARRIED_FILING_JOINTLY

    for item in f.document_inventory:
        doc_type = normalize_type(item.type)
        if doc_type not in IN_SCOPE_TYPES:
            what = OUT_OF_SCOPE_TYPES.get(doc_type, "a document SilverFile doesn't handle")
            run.escalate(f"out_of_scope_document.{doc_type}", "out_of_scope",
                         f"The taxpayer has a {item.type}: {what}.", ["SCOPE-01", "SCOPE-02", "ESC-01"])

    for doc in f.documents:
        routing.append(_check_document(run, doc, status, joint))

    # ESC-02: the taxpayer's list against what was actually supplied.
    if f.documents_supplied and f.document_inventory:
        said: dict[str, int] = {}
        for item in f.document_inventory:
            said[normalize_type(item.type)] = said.get(normalize_type(item.type), 0) + item.count
        got: dict[str, int] = {}
        for doc in f.documents:
            got[normalize_type(doc.document_type)] = got.get(normalize_type(doc.document_type), 0) + 1
        for doc_type in sorted(set(said) | set(got)):
            if doc_type in IN_SCOPE_TYPES and said.get(doc_type, 0) != got.get(doc_type, 0):
                run.escalate("document_count_mismatch", "user_document_conflict",
                             f"The taxpayer listed {said.get(doc_type, 0)} {doc_type} form(s) but "
                             f"{got.get(doc_type, 0)} were supplied.", ["ESC-02"], document_id=doc_type)
    return routing


def _check_document(run: _Run, doc: DocumentFacts, status: FilingStatus | None, joint: bool) -> DocumentRouting:
    f = run.facts
    doc_type = normalize_type(doc.document_type)
    did = doc.document_id
    out = DocumentRouting(document_id=did, document_type=doc_type, in_scope=False)

    if doc_type not in IN_SCOPE_TYPES:
        what = OUT_OF_SCOPE_TYPES.get(doc_type, "a document SilverFile doesn't handle")
        run.escalate("out_of_scope_document", "out_of_scope", f"{did} is a {doc.document_type}: {what}.",
                     ["SCOPE-02", "ESC-01"], document_id=did)
        return out
    # Extraction already escalates a W-2 for the wrong year; entered documents are checked here.
    if doc.source == "entered" and doc.tax_year is not None and doc.tax_year != TAX_YEAR:
        run.escalate("prior_year_document", "out_of_scope",
                     f"{did} is for {doc.tax_year}. Prior-year and amended returns are out of scope.",
                     ["SCOPE-02", "ESC-01"], document_id=did)
        return out

    # Whose document is it? A spouse's document belongs only on a joint return.
    if doc.recipient == "spouse" and not joint and status is not None:
        run.escalate("document_recipient_conflict", "user_document_conflict",
                     f"{did} is in the spouse's name, but the return isn't joint.", ["FS-03", "ESC-02"], document_id=did)

    if doc_type == "W-2" and doc.employee_ssn_last4:
        mine = [p.ssn for p in ([f.taxpayer] + ([f.spouse] if joint and f.spouse else [])) if p.ssn]
        if mine and not any(s.replace("-", "").replace(" ", "")[-4:] == doc.employee_ssn_last4 for s in mine):
            who = "the taxpayer or spouse" if joint else "the taxpayer"
            run.escalate("w2_ssn_mismatch", "user_document_conflict",
                         f"The employee SSN on {did} (ending {doc.employee_ssn_last4}) doesn't match {who}.",
                         ["ESC-02"], document_id=did)

    lines: list[str] = []
    note = ""
    if doc_type == "W-2":
        lines = ["1z", "25a"]
    elif doc_type == "SSA-1099":
        lines = ["6a", "6b"]
    elif doc_type == "1099-INT":
        lines = ["2a", "2b", "25b"]
        if doc.has_amount("6"):
            run.escalate("foreign_tax_paid", "out_of_scope", f"{did} shows foreign tax paid (box 6): foreign income.",
                         ["SCOPE-02", "ESC-01"], document_id=did)
            return out
    elif doc_type == "1099-DIV":
        lines = ["3a", "3b", "25b"]
        if any(doc.has_amount(b) for b in ("2b", "2c", "2d")):
            run.escalate("capital_gains_schedule_d", "out_of_scope",
                         f"{did} has capital gain amounts in boxes 2b to 2d, which need Schedule D.",
                         ["SCOPE-02", "ESC-01"], document_id=did)
            return out
        if doc.has_amount("7"):
            run.escalate("foreign_tax_paid", "out_of_scope", f"{did} shows foreign tax paid (box 7): foreign income.",
                         ["SCOPE-02", "ESC-01"], document_id=did)
            return out
        if doc.has_amount("2a"):
            note = "Box 2a capital gain distributions go on line 7a as a direct entry (Schedule D not required)."
    elif doc_type == "1099-R":
        if doc.boxes.get("2b_taxable_not_determined") is True:
            run.escalate("taxable_amount_not_determined", "taxable_amount_not_determined",
                         f"{did} has 'taxable amount not determined' checked in box 2b. Working out the taxable "
                         "part needs the Simplified Method or General Rule, which are out of scope.",
                         ["SCOPE-03", "ESC-03"], document_id=did)
            return out
        if doc.boxes.get("2b_taxable_not_determined") is None:
            run.need(f"document.{did}.2b_taxable_not_determined",
                     "A checked 'taxable amount not determined' box always escalates.", ["SCOPE-03"])
        codes = parse_distribution_codes(doc.boxes.get("7"))
        if not codes:
            run.need(f"document.{did}.7", "The box 7 distribution code decides whether the form is in scope.", ["SCOPE-04"])
            return out
        if codes not in IN_SCOPE_1099R_CODES:
            run.escalate("form_1099r_code_out_of_scope", "out_of_scope",
                         f"{did} has distribution code {''.join(sorted(codes))} in box 7. Only code 7 "
                         "(normal) and 7 with Y (charitable distribution) are in scope.",
                         ["SCOPE-04", "ESC-01"], document_id=did)
            return out
        out.qcd = "Y" in codes
        if out.qcd:
            owner = f.spouse if doc.recipient == "spouse" else f.taxpayer if doc.recipient == "taxpayer" else None
            if owner is None:
                run.need(f"document.{did}.recipient", "The charitable distribution age rule depends on whose IRA it is.", ["SCOPE-05"])
            elif owner.date_of_birth and not reached_70_and_a_half_by(owner.date_of_birth, YEAR_END):
                run.escalate("qcd_age_conflict", "user_document_conflict",
                             f"{did} is coded as a qualified charitable distribution, but the owner wasn't 70½ "
                             "by the end of 2025.", ["SCOPE-05", "ESC-02"], document_id=did)
        ira = doc.boxes.get("ira_sep_simple")
        if ira is None:
            run.need(f"document.{did}.ira_sep_simple", "The IRA/SEP/SIMPLE box decides line 4 or line 5.", ["SCOPE-04"])
            return out
        lines = ["4a", "4b", "25b"] if ira else ["5a", "5b", "25b"]
        if out.qcd:
            note = "Qualified charitable distribution: line 4c box 2 applies."

    out.in_scope = True
    out.form_lines = lines
    out.note = note
    run.find(f"document.{did}", "in scope", f"{doc_type} feeds lines {', '.join(lines)}." + (f" {note}" if note else ""),
             ["SCOPE-01"] + (["SCOPE-04"] if doc_type == "1099-R" else []))
    return out


def _schedules(run: _Run, senior: dict | None) -> list[ScheduleDecision]:
    """SCH-01."""
    f = run.facts
    entered = {s.upper().replace("SCHEDULE", "").strip() for s in f.schedules_entered}
    out = [
        ScheduleDecision(schedule=f"Schedule {n}", applies=n in entered,
                         reason="The taxpayer entered amounts on it." if n in entered else "No amounts entered.",
                         rule_ids=["SCH-01"])
        for n in ("1", "2", "3")
    ]
    if senior is None:
        out.append(ScheduleDecision(schedule="Schedule 1-A", applies=None,
                                    reason="Waiting on who qualifies for the senior deduction.", rule_ids=["SCH-01", "ELIG-03"]))
    else:
        applies = senior["taxpayer_qualifies"] or senior["spouse_qualifies"]
        out.append(ScheduleDecision(schedule="Schedule 1-A", applies=applies,
                                    reason="Someone qualifies for the senior deduction." if applies
                                    else "No one qualifies for the senior deduction.", rule_ids=["SCH-01", "ELIG-03"]))
    lines = {k: f.computed_lines.get(k) for k in ("2b", "3b")}
    if any(v is None for v in lines.values()):
        out.append(ScheduleDecision(schedule="Schedule B", applies=None,
                                    reason="Decided once Computation has lines 2b and 3b (over $1,500 needs it).",
                                    rule_ids=["SCH-01"]))
    else:
        over = [k for k, v in lines.items() if v > SCHEDULE_B_THRESHOLD]
        out.append(ScheduleDecision(schedule="Schedule B", applies=bool(over),
                                    reason=f"Line(s) {', '.join(over)} over $1,500." if over
                                    else "Lines 2b and 3b are $1,500 or less.", rule_ids=["SCH-01"]))
    out += [ScheduleDecision(schedule=f"Schedule {n}", applies=False, reason="Out of scope for SilverFile.",
                             rule_ids=["SCOPE-02"]) for n in ("A", "C", "D", "E")]
    return out


def evaluate(facts: EligibilityFacts) -> EligibilityDecision:
    run = _Run(facts)
    status = _check_filing_status(run)
    _check_ssns(run, status or facts.filing_status)
    _check_chart_applies(run, status or facts.filing_status)
    boxes, senior = _check_1040sr_and_boxes(run, status)
    documents = _check_documents(run, status or facts.filing_status)
    schedules = _schedules(run, senior)

    eligible = next((x.value for x in reversed(run.findings) if x.topic == "form_1040sr_eligible"), None)
    if run.escalations:
        outcome = "escalated"
    elif run.missing:
        outcome = "needs_facts"
    else:
        outcome = "complete"

    computation_inputs = None
    if outcome == "complete" and boxes is not None and senior is not None:
        computation_inputs = {
            "filing_status": status.value,
            **boxes,
            **senior,
            "lived_apart_from_spouse_all_year": facts.lived_apart_from_spouse_all_year,
        }
    return EligibilityDecision(
        status=outcome,
        filing_status=status,
        form_1040sr_eligible=eligible,
        line_12d=boxes,
        senior_deduction=senior,
        computation_inputs=computation_inputs,
        documents=documents,
        schedules=schedules,
        findings=run.findings,
        missing_facts=list(run.missing.values()),
        escalations=run.escalations,
    )
