"""Intake: the only agent that talks to the taxpayer. Collects filing status, age, blindness,
dependents, the document inventory and estimated payments. It may not do arithmetic or
interpret tax rules.

How it works
------------
Intake is a small LangGraph graph that asks one question per super-step. Each question is a
LangGraph ``interrupt()``: the graph pauses, the checkpointer saves the session, and the
taxpayer's answer comes back with ``Command(resume=answer)``. Because every step is
checkpointed under the session's ``thread_id`` (see ``memory.py``), a session can be abandoned
at any question and resumed later at exactly that question.

The questions are not a fixed list. ``question_plan`` derives them from the answers so far
(spouse questions only for married statuses, one block per dependent, one question per
estimated payment), so a correction to an earlier answer adds or removes the questions that
depend on it.

On every answer, before anything is stored, Intake:

1. rejects any real-looking SSN (only test SSNs in the 900-999 range are accepted) and keeps
   it out of the transcript and the checkpoint;
2. flags requests for tax advice, out-of-scope situations and signs of financial exploitation
   for a human, without answering them;
3. handles corrections ("change my date of birth", "change filing status to single", "back");
4. parses the answer for the current question, or asks again with a hint.

Intake records what the taxpayer says. It never sums, computes ages or decides eligibility:
estimated payments are recorded one by one for Computation to total, and dates of birth are
passed on as dates for Eligibility & Rules to interpret.

Contract with the Orchestrator
------------------------------
Input (``IntakeInput``): ``session_id``, plus optionally the answers, corrections and
escalations from an earlier run, ``intake_reask`` (fields to ask, for example after Review
finds a conflict with a document, or facts Eligibility & Rules or Computation need) and
``intake_notice`` (a sentence to show the taxpayer explaining why). ``human_decisions`` is
read, never written, to know which of Intake's flags a person has closed.

Requested fields (``intake_reask``) are handled like this, so a request never disappears:

* a question on the standard plan is asked again;
* a question in ``ADDITIONAL_QUESTIONS`` (facts Eligibility & Rules asks for) is added to the
  plan, and its answer is kept in ``intake_answers`` under the requested field name, which is
  where the requesting agent reads it;
* anything else (for example ``document.<id>.<box>``) raises an ``unsupported_request`` flag;
* a field that has already been answered but is requested more than
  ``MAX_REQUESTS_PER_FIELD`` times raises a ``repeated_request`` flag instead of asking again.

Output (``IntakeOutput``): ``intake_status`` ("complete", or "awaiting_human" when the
interview stopped for a person), ``intake_record`` (validated ``IntakeRecord`` as JSON),
``intake_answers``, ``intake_corrections``, ``intake_escalations``, ``intake_requests`` (what
happened to each requested field), ``intake_message`` (what the taxpayer was told when the
interview stopped) and ``needs_human_review``.

Suspected financial exploitation, or the taxpayer asking for a person, stops the interview at
once (``intake_hold``) until a person has decided on the flag.

Use ``build_intake_graph()`` either standalone with a checkpointer, driven by
``start_or_resume`` and ``submit_answer``, or as a subgraph node in the Orchestrator's graph
(compile it without a checkpointer and it inherits the parent's).
"""

from __future__ import annotations

import argparse
import operator
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field

from memory import (
    SSN_LIKE,
    load_session,
    make_checkpointer,
    redact_ssns,
    session_config,
)
from state import intake_escalation_id
from tax_math.models import FilingStatus

TAX_YEAR = 2025
EARLIEST_BIRTH_DATE = date(1900, 1, 1)
LATEST_BIRTH_DATE = date(TAX_YEAR, 12, 31)
MAX_DEPENDENTS = 10
MAX_ESTIMATED_PAYMENTS = 12

# --- Schemas -------------------------------------------------------------------------------

EscalationType = Literal[
    "tax_advice_request",
    "out_of_scope",
    "conflicting_information",
    "financial_exploitation",
    "human_requested",  # the taxpayer asked to speak with a person
    "unsupported_request",  # another agent asked for something Intake can't collect
    "repeated_request",  # another agent keeps asking for an answer Intake already has
]

# Flags that stop the interview until a person has decided on them.
BLOCKING_ESCALATIONS = {"financial_exploitation", "human_requested"}


class Escalation(TypedDict):
    type: EscalationType
    reason: str
    field: str | None  # the question being asked when it came up
    excerpt: str  # what the taxpayer said, SSNs redacted
    at: str  # ISO 8601 UTC


class Correction(TypedDict):
    field: str
    old: Any
    new: Any
    at: str


class RequestStatus(TypedDict):
    """One field another agent asked for through ``intake_reask``."""

    requested: int  # times Intake was sent this field
    status: Literal["pending", "answered", "unsupported", "repeated"]
    cleared: int  # repeated_request flags a person has decided on and Intake has acted on


class IntakeInput(TypedDict, total=False):
    session_id: str
    intake_answers: dict[str, Any]
    intake_corrections: list[Correction]
    intake_escalations: list[Escalation]
    intake_reask: list[str]
    intake_notice: str
    intake_requests: dict[str, RequestStatus]
    human_decisions: list[dict[str, Any]]  # read only: which of Intake's flags a person closed


class IntakeOutput(TypedDict, total=False):
    session_id: str
    # "awaiting_human": the interview stopped for a flag a person must see first.
    intake_status: Literal["in_progress", "complete", "awaiting_human"]
    intake_record: dict[str, Any] | None
    intake_answers: dict[str, Any]
    intake_corrections: list[Correction]
    intake_escalations: list[Escalation]
    intake_requests: dict[str, RequestStatus]
    intake_message: str  # the last thing said to the taxpayer when the interview stopped
    needs_human_review: bool


class IntakeState(IntakeInput, IntakeOutput, total=False):
    # Redacted conversation log. Internal: kept in the checkpoint, not passed to other agents.
    intake_transcript: Annotated[list[dict[str, Any]], operator.add]


class PersonInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    ssn: str = Field(description="Test SSN, 9XX-XX-XXXX.")
    date_of_birth: date
    legally_blind: bool


class Dependent(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    relationship: str = Field(description="As the taxpayer described it, e.g. 'granddaughter'.")
    date_of_birth: date
    ssn: str


class DocumentItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str = Field(description="Form name, e.g. 'W-2', or 'other'.")
    count: int = Field(ge=1)
    in_scope: bool = Field(description="False means a preparer was asked to review it.")
    description: str | None = None


class EstimatedPayment(BaseModel):
    model_config = ConfigDict(frozen=True)

    amount: Decimal = Field(gt=0, description="As reported. Intake never totals payments.")


class IntakeRecord(BaseModel):
    """What Intake hands to the rest of the system. Facts as the taxpayer stated them."""

    model_config = ConfigDict(frozen=True)

    tax_year: int = TAX_YEAR
    filing_status: FilingStatus
    lived_apart_from_spouse_all_year: bool | None = Field(
        default=None, description="Asked only for married filing separately (line 6d)."
    )
    taxpayer: PersonInfo
    spouse: PersonInfo | None = None
    dependents: list[Dependent] = []
    documents: list[DocumentItem] = []
    estimated_payments: list[EstimatedPayment] = []
    additional_facts: dict[str, bool | int] = Field(
        default={}, description="Answers to questions other agents asked for (ADDITIONAL_QUESTIONS)."
    )
    confirmed_by_taxpayer: bool


OUTPUT_KEYS = tuple(IntakeOutput.__annotations__)

# --- The question plan ---------------------------------------------------------------------

MARRIED = {FilingStatus.MARRIED_FILING_JOINTLY.value, FilingStatus.MARRIED_FILING_SEPARATELY.value}
DEPENDENT_ATTRS = ("name", "relationship", "dob", "ssn")

# Questions Intake asks only when another agent requests them through the Orchestrator
# (``intake_reask``). Keys are the field names the requesting agent reads back from
# ``intake_answers`` (Eligibility & Rules: ``facts.QUESTIONS``). Intake owns the wording.
# Value: (answer kind, question, short label for the summary).
ADDITIONAL_QUESTIONS: dict[str, tuple[str, str, str]] = {
    "spouse_died_in_2025": (
        "yes_no",
        "Did your husband or wife pass away during 2025? (yes/no)",
        "Spouse passed away in 2025",
    ),
    "spouse_death_year": (
        "year",
        "In what year did your husband or wife pass away? (for example 2024)",
        "Year your spouse passed away",
    ),
    "remarried_before_end_of_2025": (
        "yes_no",
        "Did you remarry before the end of 2025? (yes/no)",
        "Remarried before the end of 2025",
    ),
    "can_be_claimed_as_dependent": (
        "yes_no",
        "Can anyone else claim you as a dependent on their tax return? (yes/no)",
        "Someone else can claim you as a dependent",
    ),
    "spouse_can_be_claimed_as_dependent": (
        "yes_no",
        "Can anyone else claim your husband or wife as a dependent on their tax return? (yes/no)",
        "Someone else can claim your spouse as a dependent",
    ),
    "dual_status_alien": (
        "yes_no",
        "During 2025, were you a U.S. citizen or resident for only part of the year? (yes/no)",
        "U.S. citizen or resident for only part of 2025",
    ),
    "spouse_itemizes": (
        "yes_no",
        "Is your husband or wife itemizing deductions on their own return? (yes/no)",
        "Spouse itemizes on their own return",
    ),
    "spouse_had_income": (
        "yes_no",
        "Did your husband or wife have any income in 2025? (yes/no)",
        "Spouse had income in 2025",
    ),
    "spouse_filing_return": (
        "yes_no",
        "Is your husband or wife filing their own tax return for 2025? (yes/no)",
        "Spouse is filing their own return",
    ),
    "paid_over_half_home_cost": (
        "yes_no",
        "Did you pay more than half the cost of keeping up your home in 2025? (yes/no)",
        "Paid more than half the cost of your home",
    ),
    "child_lived_with_you_all_year": (
        "yes_no",
        "Did your child live with you for all of 2025? (yes/no)",
        "Your child lived with you all year",
    ),
}

# Other agents' names for fields Intake already asks under its own name.
REQUEST_ALIASES = {
    "lived_apart_from_spouse_all_year": "lived_apart_all_year",  # Computation
}

MAX_REQUESTS_PER_FIELD = 3  # after this many requests for an answered field, ask a person


def canonical_field(field: str) -> str:
    return REQUEST_ALIASES.get(field, field)


def question_plan(answers: dict[str, Any], requested: Any = ()) -> list[str]:
    """Every question that applies, in order, given the answers so far.

    ``requested``: fields other agents have asked for. Additional questions appear in the plan
    once requested, and stay in it once answered, so their answers are never pruned.
    """
    status = answers.get("filing_status")
    plan = ["filing_status"]
    if status == FilingStatus.MARRIED_FILING_SEPARATELY.value:
        plan.append("lived_apart_all_year")
    plan += ["taxpayer_ssn", "taxpayer_dob", "taxpayer_blind"]
    if status in MARRIED:
        plan += ["spouse_ssn", "spouse_dob", "spouse_blind"]
    plan.append("has_dependents")
    if answers.get("has_dependents"):
        plan.append("dependent_count")
        for n in range(1, (answers.get("dependent_count") or 0) + 1):
            plan += [f"dependent.{n}.{attr}" for attr in DEPENDENT_ATTRS]
    plan += ["documents", "has_estimated_payments"]
    if answers.get("has_estimated_payments"):
        plan.append("estimated_payment_count")
        for n in range(1, (answers.get("estimated_payment_count") or 0) + 1):
            plan.append(f"estimated_payment.{n}.amount")
    requested = {canonical_field(f) for f in requested}
    plan += [f for f in ADDITIONAL_QUESTIONS if f in answers or f in requested]
    plan.append("confirm")
    return plan


def next_question(state: IntakeState) -> str | None:
    """The field to ask about next: a requested re-ask first, then the first unanswered one."""
    answers = state.get("intake_answers") or {}
    reask = state.get("intake_reask") or []
    plan = question_plan(answers, reask)
    for field in reask:
        if field in plan:
            return field
    for field in plan:
        if field not in answers:
            return field
    return None


def field_kind(field: str) -> str:
    if field in ADDITIONAL_QUESTIONS:
        return ADDITIONAL_QUESTIONS[field][0]
    if field == "filing_status":
        return "filing_status"
    if field in {
        "lived_apart_all_year",
        "taxpayer_blind",
        "spouse_blind",
        "has_dependents",
        "has_estimated_payments",
    }:
        return "yes_no"
    if field.endswith("ssn"):
        return "ssn"
    if field.endswith("dob"):
        return "date"
    if field in {"dependent_count", "estimated_payment_count"}:
        return "count"
    if field.endswith(".name"):
        return "name"
    if field.endswith(".relationship"):
        return "relationship"
    if field.endswith(".amount"):
        return "amount"
    return field  # "documents", "confirm"


def _dependent_name(answers: dict[str, Any], n: str) -> str:
    return answers.get(f"dependent.{n}.name") or f"dependent {n}"


TEST_SSN_HINT = (
    "SilverFile is a test system: please use the assigned test number, which starts with 9 "
    "(format 9XX-XX-XXXX). Never enter a real Social Security number."
)


def question_text(field: str, answers: dict[str, Any]) -> str:
    fixed = {
        "filing_status": (
            "What is your filing status for 2025? Please answer with one of: single, married "
            "filing jointly, married filing separately, head of household, or qualifying "
            "surviving spouse."
        ),
        "lived_apart_all_year": "Did you live apart from your spouse for all of 2025? (yes/no)",
        "taxpayer_ssn": f"What is your Social Security number? {TEST_SSN_HINT}",
        "taxpayer_dob": "What is your date of birth? (for example 03/14/1958)",
        "taxpayer_blind": "Are you legally blind? (yes/no)",
        "spouse_ssn": f"What is your spouse's Social Security number? {TEST_SSN_HINT}",
        "spouse_dob": "What is your spouse's date of birth? (for example 03/14/1958)",
        "spouse_blind": "Is your spouse legally blind? (yes/no)",
        "has_dependents": "Do you have any dependents? (yes/no)",
        "dependent_count": "How many dependents do you have?",
        "documents": (
            "Which 2025 tax documents do you have? Please list them all, for example "
            "'2 W-2s, an SSA-1099 and a 1099-INT'. Say 'none' if you have none."
        ),
        "has_estimated_payments": ("Did you make any estimated tax payments for 2025? (yes/no)"),
        "estimated_payment_count": "How many estimated tax payments did you make for 2025?",
    }
    if field in fixed:
        return fixed[field]
    if field in ADDITIONAL_QUESTIONS:
        return ADDITIONAL_QUESTIONS[field][1]
    if field.startswith("dependent."):
        _, n, attr = field.split(".")
        who = _dependent_name(answers, n)
        return {
            "name": f"What is dependent {n}'s full name?",
            "relationship": (
                f"What is {who}'s relationship to you? (for example son, daughter, "
                "grandchild or parent)"
            ),
            "dob": f"What is {who}'s date of birth?",
            "ssn": f"What is {who}'s Social Security number? {TEST_SSN_HINT}",
        }[attr]
    if field.startswith("estimated_payment."):
        n = field.split(".")[1]
        return f"How much was estimated payment {n}? (for example $1,250.00)"
    if field == "confirm":
        return (
            "Here is what I have:\n"
            + summarize_answers(answers)
            + "\n\nIs everything correct? Reply 'yes' to finish, or tell me what to change "
            "(for example 'change date of birth')."
        )
    raise KeyError(field)


# --- Parsing (no arithmetic, no tax rules) -------------------------------------------------


class ParseError(ValueError):
    """The answer couldn't be understood. The message is shown to the taxpayer."""


FILING_STATUS_WORDS = [
    (
        r"\bqualifying (surviving spouse|widow(er)?)\b|\bsurviving spouse\b|\bqss\b|\bwidow(er)?\b",
        FilingStatus.QUALIFYING_SURVIVING_SPOUSE,
    ),
    (r"\bhead of (the )?household\b|\bhoh\b", FilingStatus.HEAD_OF_HOUSEHOLD),
    (r"\bjoint(ly)?\b|\bmfj\b", FilingStatus.MARRIED_FILING_JOINTLY),
    (r"\bseparate(ly)?\b|\bmfs\b", FilingStatus.MARRIED_FILING_SEPARATELY),
    (r"\bsingle\b", FilingStatus.SINGLE),
]


def parse_filing_status(text: str) -> str:
    t = text.lower()
    found = {status for pattern, status in FILING_STATUS_WORDS if re.search(pattern, t)}
    if len(found) == 1:
        return found.pop().value
    if not found and re.search(r"\bmarried\b", t):
        raise ParseError("Are you married filing jointly, or married filing separately?")
    raise ParseError(
        "Please choose one: single, married filing jointly, married filing separately, "
        "head of household, or qualifying surviving spouse."
    )


_UNSURE = re.compile(r"\b(not sure|unsure|don'?t know|do not know|no idea|idk|maybe)\b")
_YES_FIRST = {"yes", "y", "yeah", "yep", "yup", "yea", "correct", "sure", "true"}
_NO_FIRST = {"no", "n", "nope", "nah", "false", "never"}


def parse_yes_no(text: str) -> bool:
    t = text.lower().strip()
    if _UNSURE.search(t):
        raise ParseError(
            "That's okay. If you need to check, type 'pause' and come back later; your answers "
            "are saved. Otherwise, please answer yes or no."
        )
    words = re.findall(r"[a-z']+", t)
    if words and words[0] in _YES_FIRST:
        return True
    if words and words[0] in _NO_FIRST:
        return False
    if re.search(r"\b(not|never|no)\b|n't\b", t):
        return False
    if re.search(r"\b(i am|i'm|i do|i did|we did|she is|he is|yes)\b", t):
        return True
    raise ParseError("Please answer yes or no.")


def is_test_ssn(area: str, group: str, serial: str) -> bool:
    """Only SilverFile's test range: area 900-999, and never an all-zero group or serial."""
    return area.startswith("9") and group != "00" and serial != "0000"


def parse_ssn(text: str) -> str:
    matches = SSN_LIKE.findall(text)
    if len(matches) != 1:
        raise ParseError(f"Please enter one Social Security number. {TEST_SSN_HINT}")
    area, group, serial = matches[0]
    if not is_test_ssn(area, group, serial):  # normally already caught by the SSN guard
        raise ParseError(TEST_SSN_HINT)
    return f"{area}-{group}-{serial}"


_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%m.%d.%Y",
    "%B %d, %Y",
    "%B %d %Y",
    "%b %d, %Y",
    "%b %d %Y",
    "%d %B %Y",
    "%d %b %Y",
)


def parse_date(text: str) -> str:
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text.strip(), flags=re.I)
    t = re.sub(r"\s+", " ", t.replace(".,", ",")).strip(" .")
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(t, fmt).date()
        except ValueError:
            continue
        if not EARLIEST_BIRTH_DATE <= parsed <= LATEST_BIRTH_DATE:
            raise ParseError(
                f"That date doesn't look right. Please enter a date between 1900 and "
                f"{TAX_YEAR}, for example 03/14/1958."
            )
        return parsed.isoformat()
    if re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2}", t):
        raise ParseError("Please include the full four-digit year, for example 03/14/1958.")
    raise ParseError("Please enter the date as month/day/year, for example 03/14/1958.")


_NUMBER_WORDS = {
    "zero": 0,
    "none": 0,
    "no": 0,
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


def parse_count(text: str, maximum: int) -> int:
    t = text.lower().strip()
    m = re.search(r"\b(\d{1,3})\b", t)
    if m:
        n = int(m.group(1))
    else:
        words = [w for w in re.findall(r"[a-z]+", t) if w in _NUMBER_WORDS and w not in {"a", "an"}]
        if len(words) != 1:
            raise ParseError("Please answer with a number, for example 2.")
        n = _NUMBER_WORDS[words[0]]
    if n < 1:
        raise ParseError(
            "You said there is at least one. Please enter how many, or type 'back' to change "
            "your earlier answer."
        )
    if n > maximum:
        raise ParseError(
            f"SilverFile can record up to {maximum}. Please enter a number from 1 to {maximum}."
        )
    return n


_NAME = re.compile(r"^[A-Za-z][A-Za-z .,'-]*$")


def parse_name(text: str, *, what: str = "name", max_len: int = 80) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    if not t or len(t) > max_len or not _NAME.match(t):
        raise ParseError(f"Please enter the {what} using letters only.")
    return t


def parse_amount(text: str) -> str:
    t = text.strip().lower().replace("dollars", "").replace("usd", "").strip()
    m = re.fullmatch(r"\$?\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d{1,2})?", t)
    if not m:
        raise ParseError("Please enter the amount in dollars, for example $1,250.00.")
    amount = Decimal(m.group(1).replace(",", "") + (m.group(2) or ""))
    if amount <= 0:
        raise ParseError("Please enter an amount greater than zero.")
    return str(amount)


def parse_year(text: str) -> int:
    years = re.findall(r"\b(1[89]\d\d|20\d\d)\b", text)
    if len(years) != 1:
        raise ParseError("Please enter the year with four digits, for example 2024.")
    year = int(years[0])
    if not EARLIEST_BIRTH_DATE.year <= year <= TAX_YEAR:
        raise ParseError(f"Please enter a year between 1900 and {TAX_YEAR}.")
    return year


# Document inventory. Provisional until the scope rules land in data/policy.md (David).
_COUNT = r"(?:(\d{1,2}|an?|one|two|three|four|five|six|seven|eight|nine|ten)\s+)?"
IN_SCOPE_DOCUMENTS = {
    "W-2": r"w[- ]?2s?",
    "SSA-1099": r"ssa[- ]?1099s?|social security (?:benefits? )?statements?",
    "1099-INT": r"1099[- ]?ints?",
    "1099-DIV": r"1099[- ]?divs?",
    "1099-R": r"1099[- ]?rs?",
    "1098-E": r"1098[- ]?es?",
}
OUT_OF_SCOPE_DOCUMENTS = {
    "1099-NEC": r"1099[- ]?necs?",
    "1099-MISC": r"1099[- ]?miscs?",
    "1099-K": r"1099[- ]?ks?",
    "1099-B": r"1099[- ]?bs?",
    "1099-S": r"1099[- ]ss?",
    "1099-G": r"1099[- ]?gs?",
    "Schedule K-1": r"(?:schedule )?k[- ]?1s?",
    "1098": r"1098(?![- ]?e)",
    "1095-A": r"1095[- ]?as?",
    "SSA-1042S": r"ssa[- ]?1042[- ]?s",
}
_ALL_DOCUMENTS = {**IN_SCOPE_DOCUMENTS, **OUT_OF_SCOPE_DOCUMENTS}
_OTHER = r"\bother(?:\s+(?:documents?|forms?))?\s*:"


def parse_documents(text: str) -> list[dict[str, Any]]:
    t = text.lower()
    if re.fullmatch(r"\s*(none|no|nothing|no documents|i have none|i don'?t have any)\.?\s*", t):
        return []
    found: dict[str, int] = {}
    consumed = t
    for name, pattern in _ALL_DOCUMENTS.items():
        for m in re.finditer(rf"\b{_COUNT}(?:{pattern})\b", consumed):
            word = m.group(1)
            found[name] = found.get(name, 0) + (
                int(word) if word and word.isdigit() else _NUMBER_WORDS.get(word or "one", 1)
            )
        consumed = re.sub(rf"\b{_COUNT}(?:{pattern})\b", " ", consumed)
    docs = [{"type": name, "count": count} for name, count in found.items()]
    if re.search(_OTHER, t):
        other = re.sub(rf"^.*?{_OTHER}\s*", "", text, flags=re.I | re.S).strip()
        docs.append({"type": "other", "count": 1, "description": redact_ssns(other)[:120] or None})
    if not docs:
        if re.search(r"\b1099s?\b", consumed):
            raise ParseError(
                "Which kind of 1099 is it? For example 1099-INT, 1099-DIV, 1099-R or SSA-1099."
            )
        raise ParseError(
            "I didn't recognize those documents. Please list forms such as W-2, SSA-1099, "
            "1099-INT, 1099-DIV or 1099-R. For anything else, start with 'other:' and describe "
            "it, or say 'none'."
        )
    return docs


def document_in_scope(doc: dict[str, Any]) -> bool:
    return doc["type"] in IN_SCOPE_DOCUMENTS


def parse_answer(field: str, text: str) -> Any:
    kind = field_kind(field)
    if kind == "filing_status":
        return parse_filing_status(text)
    if kind == "yes_no":
        return parse_yes_no(text)
    if kind == "ssn":
        return parse_ssn(text)
    if kind == "date":
        return parse_date(text)
    if kind == "count":
        maximum = MAX_DEPENDENTS if field == "dependent_count" else MAX_ESTIMATED_PAYMENTS
        return parse_count(text, maximum)
    if kind == "name":
        return parse_name(text)
    if kind == "relationship":
        return parse_name(text, what="relationship", max_len=40).lower()
    if kind == "amount":
        return parse_amount(text)
    if kind == "year":
        return parse_year(text)
    if kind == "documents":
        return parse_documents(text)
    if kind == "confirm":
        return parse_yes_no(text)
    raise KeyError(field)


# --- Escalation detection ------------------------------------------------------------------

TAX_ADVICE_PATTERNS = [
    r"\bshould (i|we) (file|claim|choose|pick|select|take|deduct|itemi[sz]e|report|list|include|go with)\b",
    r"\b(which|what) (filing status|status|option|one) (is|would be|should)\b",
    r"\b(better|best|cheaper|smarter) (for me|for us|option|choice|filing status|way)\b",
    r"\b(save|lower|reduce|minimi[sz]e|cut|avoid) (on )?(my |our )?tax(es)?\b",
    r"\b(can|could|may) (i|we) (deduct|claim|write off|exclude)\b",
    r"\btax advice\b|\badvise me\b",
    r"\b(what|which) do you (recommend|suggest|advise)\b|\bdo you (recommend|suggest)\b",
    r"\bhow much (will|would|do) (i|we) (owe|get back|pay|get)\b",
    r"\b(will|would) (i|we) (get|owe) (a )?(refund|money)\b",
    r"\bis (this|that|it|my \w+) (taxable|deductible)\b",
    r"\bdo (i|we) (have|need) to (file|pay|report)\b",
]

OUT_OF_SCOPE_PATTERNS = [
    (r"\bself[- ]employ", "self-employment income"),
    (r"\b(my|our|a|small|side) business\b|\bbusiness income\b|\bschedule c\b", "business income"),
    (r"\brental (income|property)\b|\blandlord\b|\brent out\b", "rental income"),
    (r"\bcrypto|\bbitcoin\b|\bdigital assets?\b", "digital assets"),
    (
        r"\bforeign (income|pension|account|bank)|\boverseas (income|account)|\bfbar\b",
        "foreign income or accounts",
    ),
    (
        r"\bsold (my |our |a |some )?(house|home|stocks?|shares|property|land|business)\b"
        r"|\bcapital gains?\b",
        "sale of property or capital gains",
    ),
    (r"\bitemi[sz]", "itemized deductions"),
    (
        r"\bamend|\bprior[- ]year\b|\bback taxes\b|\b20(1\d|2[0-4]) (return|taxes)\b",
        "another tax year or an amended return",
    ),
    (
        r"\bpartnership\b|\bs[- ]corp|\btrust income\b|\bestate income\b",
        "partnership, trust or estate income",
    ),
    (r"\bfarm(ing)? income\b|\bschedule f\b", "farm income"),
    (r"\bdual[- ]status\b|\bnon-?resident\b", "residency status"),
    (
        r"\bclaims? me as (a|their|his|her) dependent\b|\bclaimed me\b"
        r"|\b(i am|i'm) (a|someone'?s|their|his|her) dependent\b",
        "the taxpayer may be someone else's dependent",
    ),
    (
        r"\b(spouse|husband|wife) (died|passed( away)?)\b|\b(died|passed away) (in|during|this) (2025|year)\b",
        "a spouse who died during the year",
    ),
    (r"\bgambling\b|\bcasino\b|\blottery winnings\b", "gambling income"),
    (r"\balimony\b", "alimony"),
    (r"\bhealth savings account\b|\bhsa\b", "a health savings account"),
]

EXPLOITATION_PATTERNS = [
    r"\bgift ?cards?\b",
    r"\b(asked|told|tells|wants?|making|made|forcing|forced|pressur\w*) me to "
    r"(send|wire|transfer|give|sign|withdraw|change|hand over)\b",
    r"\b(wire|send|transfer) (them |him |her )?(money|cash|funds|my refund)\b",
    r"\b(pressur\w*|threaten\w*|forc\w*|bully\w*) (me|us)\b",
    r"\b(threatened|threatening)\b",
    r"\b(afraid|scared) of (him|her|them|my)\b",
    r"\b(took|takes|taking|stole|steals|stealing|keeps|kept|spends|spent|spending) "
    r"(all )?(of )?my (money|checks?|benefits|social security|savings|refund|pension)\b",
    r"\b(won'?t|doesn'?t|don'?t|will not|does not) (let|allow) me (to )?(see|access|use|have|touch)\b",
    r"\bcontrols? (all )?(of )?my (money|finances|bank|accounts?|checks?)\b",
    r"\bdon'?t tell (anyone|anybody|my|the)\b|\bkeep (it|this) (a )?secret\b",
    r"\b(irs|social security|medicare)( agent| office)? (called|calls|emailed|texted|is calling)\b"
    r".*\b(pay|arrest|gift|wire|demand)",
    r"\b(prize|lottery|sweepstakes)\b.*\b(fee|pay|send|upfront)\b",
    r"\bsign(ed)? over\b",
    r"\bchang\w* (my )?(bank|direct deposit)\w*\b.*\b(his|her|their)\b",
]

HUMAN_REQUEST_PATTERNS = [
    r"\b(speak|talk|chat)\w* (to|with) (a |an |someone|somebody|a real |an actual )?"
    r"(real |actual |live )?(human|person|people|preparer|representative|agent|staff|someone|somebody)\b",
    r"\b(real|actual|live) (human|person)\b|\bhuman being\b",
    r"\b(want|need|get|give me|connect me (to|with)) (a |an )?(human|person|representative|operator)\b",
    r"\bcan (i|we) (speak|talk) to (someone|somebody|anyone)\b",
]

ESCALATION_NOTICES = {
    "human_requested": (
        "Of course. I've asked a Kushim staff member to contact you. Your answers are saved, "
        "and nothing more will be done on your return until you've spoken with them."
    ),
    "tax_advice_request": (
        "I'm not able to give tax advice or say which choice is better. I've passed your "
        "question to a Kushim tax preparer, who will follow up with you."
    ),
    "out_of_scope": (
        "Thank you for mentioning that. It's outside what SilverFile can prepare ({reason}), "
        "so I've flagged it for a Kushim preparer to review."
    ),
    "financial_exploitation": (
        "Thank you for telling me. Your safety matters, and I've asked a Kushim staff member "
        "to follow up with you privately. Your answers are saved, and nothing more will be done "
        "on your return until they have. If you are in danger right now, call 911. You can "
        "also call the National Elder Fraud Hotline at 1-833-372-8311."
    ),
}


def detect_escalations(text: str) -> list[tuple[EscalationType, str]]:
    t = text.lower()
    found: list[tuple[EscalationType, str]] = []
    if any(re.search(p, t) for p in EXPLOITATION_PATTERNS):
        found.append(("financial_exploitation", "possible financial exploitation"))
    if any(re.search(p, t) for p in HUMAN_REQUEST_PATTERNS):
        found.append(("human_requested", "taxpayer asked to speak with a person"))
    if any(re.search(p, t) for p in TAX_ADVICE_PATTERNS):
        found.append(("tax_advice_request", "taxpayer asked for tax advice"))
    for pattern, reason in OUT_OF_SCOPE_PATTERNS:
        if re.search(pattern, t):
            found.append(("out_of_scope", reason))
    return found


def make_escalation(
    kind: EscalationType, reason: str, field: str | None, text: str = ""
) -> Escalation:
    return Escalation(
        type=kind,
        reason=reason,
        field=field,
        excerpt=redact_ssns(text)[:200],
        at=_now(),
    )


_REPEAT_SUFFIX = re.compile(r" \(raised again, \d+\)$")


def _add_escalation(
    escalations: list[Escalation], new: Escalation, decided: set[str] | frozenset = frozenset()
) -> bool:
    """Add a flag unless the same one is still open.

    ``decided``: ids (``state.intake_escalation_id``) a person has already decided on. If every
    earlier flag of this kind was decided, the new one is a fresh occurrence: it gets its own
    reason, and so its own id, so the Orchestrator sends it to a person again instead of
    treating it as already handled.
    """
    same = [
        e
        for e in escalations
        if e["type"] == new["type"] and _REPEAT_SUFFIX.sub("", e["reason"]) == new["reason"]
    ]
    if any(intake_escalation_id(e) not in decided for e in same):
        return False
    if same:
        new = {**new, "reason": f"{new['reason']} (raised again, {len(same) + 1})"}
    escalations.append(new)
    return True


def _decided(state: IntakeState) -> set[str]:
    return {d.get("escalation_id") for d in state.get("human_decisions") or []}


def open_blocking_escalations(state: IntakeState) -> list[Escalation]:
    """Flags that stop the interview and that no person has decided on yet."""
    decided = _decided(state)
    return [
        e
        for e in state.get("intake_escalations") or []
        if e["type"] in BLOCKING_ESCALATIONS and intake_escalation_id(e) not in decided
    ]


_CHILD_WORDS = re.compile(r"\b(son|daughter|child|kid|stepson|stepdaughter|stepchild)\b")


def find_conflicts(answers: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """Facts that contradict each other. Returns (reason, fields involved).

    These are factual inconsistencies only. Whether a filing status or dependent qualifies is
    Eligibility & Rules' decision, not Intake's.
    """
    plan = question_plan(answers)
    conflicts: list[tuple[str, list[str]]] = []
    ssn_fields = [f for f in plan if f.endswith("ssn") and f in answers]
    seen: dict[str, str] = {}
    for f in ssn_fields:
        if answers[f] in seen:
            conflicts.append(
                (
                    f"the same SSN was given for {_label(seen[answers[f]])} and {_label(f)}",
                    [seen[answers[f]], f],
                )
            )
        else:
            seen[answers[f]] = f
    taxpayer_dob = answers.get("taxpayer_dob")
    for n in range(1, (answers.get("dependent_count") or 0) + 1):
        rel, dob = answers.get(f"dependent.{n}.relationship"), answers.get(f"dependent.{n}.dob")
        if (
            f"dependent.{n}.dob" in plan
            and rel
            and dob
            and taxpayer_dob
            and _CHILD_WORDS.search(rel)
            and dob <= taxpayer_dob
        ):
            conflicts.append(
                (
                    f"dependent {n} is described as a {rel} but was born before the taxpayer",
                    [f"dependent.{n}.relationship", f"dependent.{n}.dob"],
                )
            )
    return conflicts


# --- Corrections ---------------------------------------------------------------------------

_CORRECTION = re.compile(
    r"^\s*(?:please\s+|can you\s+|could you\s+|i (?:want|need|would like|'d like) to\s+|let me\s+)?"
    r"(?:change|correct|fix|update|edit|redo|go back to)\s+(.+?)\s*[.!?]?\s*$",
    re.I,
)
_BACK = re.compile(r"^\s*(go\s+)?(back|previous|undo)\s*[.!]?\s*$", re.I)
_PAUSE = re.compile(r"^\s*(pause|save|save and (exit|quit)|stop|quit|exit|later)\s*[.!]?\s*$", re.I)


def resolve_fields(phrase: str, answers: dict[str, Any]) -> list[str]:
    """Which field(s) a phrase like 'my spouse's date of birth' or 'dependent 2' refers to."""
    p = re.sub(r"'s\b", "", phrase.lower())
    p = re.sub(r"\b(my|the|our|answer|for|about)\b", " ", p)
    p = re.sub(r"\s+", " ", p).strip()
    attr = None
    if re.search(r"\b(ssn|social security|social)\b", p):
        attr = "ssn"
    elif re.search(r"\b(birth|dob|birthday|born|bday)\b", p):
        attr = "dob"
    elif re.search(r"\bblind", p):
        attr = "blind"

    if m := re.search(r"\bdependent\s*(?:#|number\s*|no\.?\s*)?(\d+)", p):
        n = m.group(1)
        if re.search(r"\brelationship\b", p):
            return [f"dependent.{n}.relationship"]
        if re.search(r"\bname\b", p):
            return [f"dependent.{n}.name"]
        if attr in {"ssn", "dob"}:
            return [f"dependent.{n}.{attr}"]
        return [f"dependent.{n}.{a}" for a in DEPENDENT_ATTRS]
    if m := re.search(r"\bpayment\s*(?:#|number\s*|no\.?\s*)?(\d+)", p):
        return [f"estimated_payment.{m.group(1)}.amount"]
    if re.search(r"\b(number of|how many) dependents\b", p):
        return ["dependent_count"]
    if re.search(r"\b(number of|how many) (estimated )?payments\b", p):
        return ["estimated_payment_count"]
    if re.search(r"\bfiling status\b|\bstatus\b", p):
        return ["filing_status"]
    if re.search(r"\bapart\b", p):
        return ["lived_apart_all_year"]
    spouse = bool(re.search(r"\b(spouse|husband|wife)\b", p))
    if attr:
        return [f"{'spouse' if spouse else 'taxpayer'}_{attr}"]
    if spouse:
        return ["spouse_ssn", "spouse_dob", "spouse_blind"]
    if re.search(r"\bdependents?\b", p):
        return ["has_dependents"]
    if re.search(r"\b(documents?|forms?|paperwork)\b", p):
        return ["documents"]
    if re.search(r"\b(estimated|payments?)\b", p):
        return ["has_estimated_payments"]
    return []


def _label(field: str) -> str:
    labels = {
        "filing_status": "filing status",
        "lived_apart_all_year": "living apart from your spouse",
        "taxpayer_ssn": "your SSN",
        "taxpayer_dob": "your date of birth",
        "taxpayer_blind": "your blindness status",
        "spouse_ssn": "your spouse's SSN",
        "spouse_dob": "your spouse's date of birth",
        "spouse_blind": "your spouse's blindness status",
        "has_dependents": "whether you have dependents",
        "dependent_count": "number of dependents",
        "documents": "your documents",
        "has_estimated_payments": "whether you made estimated payments",
        "estimated_payment_count": "number of estimated payments",
        "confirm": "confirmation",
    }
    if field in labels:
        return labels[field]
    if field in ADDITIONAL_QUESTIONS:
        return ADDITIONAL_QUESTIONS[field][2].lower()
    parts = field.split(".")
    if parts[0] == "dependent":
        return f"dependent {parts[1]}'s {dict(name='name', relationship='relationship', dob='date of birth', ssn='SSN')[parts[2]]}"
    return f"estimated payment {parts[1]}"


# --- Display helpers -----------------------------------------------------------------------


def mask_ssn(ssn: str) -> str:
    return f"***-**-{ssn[-4:]}"


def display_value(field: str, value: Any) -> str:
    if value is None:
        return "(not answered)"
    kind = field_kind(field)
    if kind == "ssn":
        return mask_ssn(value)
    if kind in {"yes_no", "confirm"}:
        return "Yes" if value else "No"
    if kind == "filing_status":
        return FilingStatus(value).label
    if kind == "date":
        return date.fromisoformat(value).strftime("%m/%d/%Y")
    if kind == "amount":
        return f"${Decimal(value):,.2f}"
    if kind == "documents":
        if not value:
            return "none"
        return ", ".join(
            f"{d['count']} x {d['type']}"
            + (f" ({d['description']})" if d.get("description") else "")
            + ("" if document_in_scope(d) else " [flagged for a preparer]")
            for d in value
        )
    return str(value)


def summarize_answers(answers: dict[str, Any]) -> str:
    plan = question_plan(answers)
    a = answers
    lines = [f"- Filing status: {display_value('filing_status', a.get('filing_status'))}"]
    if "lived_apart_all_year" in plan:
        lines.append(
            f"- Lived apart from spouse all of 2025: {display_value('lived_apart_all_year', a.get('lived_apart_all_year'))}"
        )
    for who, label in (("taxpayer", "You"), ("spouse", "Spouse")):
        if f"{who}_ssn" in plan:
            lines.append(
                f"- {label}: SSN {display_value(f'{who}_ssn', a.get(f'{who}_ssn'))}, born "
                f"{display_value(f'{who}_dob', a.get(f'{who}_dob'))}, legally blind: "
                f"{display_value(f'{who}_blind', a.get(f'{who}_blind'))}"
            )
    if a.get("has_dependents"):
        lines.append("- Dependents:")
        for n in range(1, (a.get("dependent_count") or 0) + 1):
            lines.append(
                f"  {n}. {a.get(f'dependent.{n}.name')} ({a.get(f'dependent.{n}.relationship')}), born "
                f"{display_value(f'dependent.{n}.dob', a.get(f'dependent.{n}.dob'))}, SSN "
                f"{display_value(f'dependent.{n}.ssn', a.get(f'dependent.{n}.ssn'))}"
            )
    else:
        lines.append("- Dependents: none")
    lines.append(f"- Documents: {display_value('documents', a.get('documents'))}")
    if a.get("has_estimated_payments"):
        lines.append("- Estimated payments:")
        for n in range(1, (a.get("estimated_payment_count") or 0) + 1):
            lines.append(
                f"  {n}. {display_value(f'estimated_payment.{n}.amount', a.get(f'estimated_payment.{n}.amount'))}"
            )
    else:
        lines.append("- Estimated payments: none")
    for f, (_kind, _question, label) in ADDITIONAL_QUESTIONS.items():
        if f in a:
            lines.append(f"- {label}: {display_value(f, a[f])}")
    conflicts = find_conflicts(answers)
    if conflicts:
        lines.append("\nPlease double-check: " + "; ".join(reason for reason, _ in conflicts) + ".")
    return "\n".join(lines)


def build_record(answers: dict[str, Any]) -> IntakeRecord:
    """Assemble the validated record from the answers that apply."""
    a = answers

    def person(who: str) -> PersonInfo:
        return PersonInfo(
            ssn=a[f"{who}_ssn"], date_of_birth=a[f"{who}_dob"], legally_blind=a[f"{who}_blind"]
        )

    status = a["filing_status"]
    return IntakeRecord(
        filing_status=status,
        lived_apart_from_spouse_all_year=a.get("lived_apart_all_year")
        if status == FilingStatus.MARRIED_FILING_SEPARATELY.value
        else None,
        taxpayer=person("taxpayer"),
        spouse=person("spouse") if status in MARRIED else None,
        dependents=[
            Dependent(
                name=a[f"dependent.{n}.name"],
                relationship=a[f"dependent.{n}.relationship"],
                date_of_birth=a[f"dependent.{n}.dob"],
                ssn=a[f"dependent.{n}.ssn"],
            )
            for n in range(1, (a.get("dependent_count") or 0) + 1)
        ]
        if a.get("has_dependents")
        else [],
        documents=[
            DocumentItem(**d, in_scope=document_in_scope(d)) for d in a.get("documents") or []
        ],
        estimated_payments=[
            EstimatedPayment(amount=a[f"estimated_payment.{n}.amount"])
            for n in range(1, (a.get("estimated_payment_count") or 0) + 1)
        ]
        if a.get("has_estimated_payments")
        else [],
        additional_facts={f: a[f] for f in ADDITIONAL_QUESTIONS if f in a},
        confirmed_by_taxpayer=bool(a.get("confirm")),
    )


# --- Turn processing -----------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _prune(answers: dict[str, Any]) -> dict[str, Any]:
    """Drop answers to questions that no longer apply (data minimization)."""
    plan = set(question_plan(answers))
    return {k: v for k, v in answers.items() if k in plan}


def _store(answers, corrections, reask, field, value, requests=None) -> None:
    old = answers.get(field)
    answers[field] = value
    if field in reask:
        reask.remove(field)
    if requests is not None and field in requests:
        requests[field] = {**requests[field], "status": "answered"}
    if field != "confirm":
        answers.pop("confirm", None)
        if old is not None and old != value:
            masked = (lambda v: mask_ssn(v)) if field_kind(field) == "ssn" else (lambda v: v)
            corrections.append(
                Correction(field=field, old=masked(old), new=masked(value), at=_now())
            )
    pruned = _prune(answers)
    answers.clear()
    answers.update(pruned)


def process_answer(state: IntakeState, field: str, raw: Any) -> dict[str, Any]:
    """Apply one taxpayer answer to the current question. Pure: returns state updates."""
    text = "" if raw is None else str(raw).strip()
    answers = dict(state.get("intake_answers") or {})
    corrections = list(state.get("intake_corrections") or [])
    escalations = list(state.get("intake_escalations") or [])
    reask = list(state.get("intake_reask") or [])
    requests = dict(state.get("intake_requests") or {})
    decided = _decided(state)
    notices: list[str] = []

    def result() -> dict[str, Any]:
        return {
            "intake_answers": answers,
            "intake_corrections": corrections,
            "intake_escalations": escalations,
            "intake_reask": [f for f in reask if f in question_plan(answers, reask)],
            "intake_requests": requests,
            "intake_notice": " ".join(notices),
            "intake_transcript": [
                {
                    "role": "assistant",
                    "field": field,
                    "content": redact_ssns(question_text(field, state.get("intake_answers") or {})),
                },
                {"role": "user", "field": field, "content": redact_ssns(text)},
            ],
        }

    if not text:
        notices.append("I didn't catch that.")
        return result()

    # 1. SSN guard: never accept or keep a real-looking SSN.
    for area, group, serial in SSN_LIKE.findall(text):
        if not is_test_ssn(area, group, serial):
            if area.startswith("9"):
                notices.append(f"That number isn't a properly formatted test SSN. {TEST_SSN_HINT}")
            else:
                notices.append(
                    "For your protection, SilverFile can't accept a real Social Security number, "
                    f"and I haven't saved it. {TEST_SSN_HINT}"
                )
            return result()

    # 2. Escalations: flag for a human, never answer.
    detected = detect_escalations(text)
    for kind, reason in detected:
        _add_escalation(escalations, make_escalation(kind, reason, field, text), decided)
        notice = ESCALATION_NOTICES[kind].format(reason=reason)
        if notice not in notices:
            notices.append(notice)
    if any(kind in BLOCKING_ESCALATIONS for kind, _ in detected):
        return result()  # nothing else from this message is used; the interview stops here
    if any(kind == "tax_advice_request" for kind, _ in detected):
        notices.append("For now, please answer the question as best you can.")
        return result()

    # 3. Commands: pause, back, corrections.
    if _PAUSE.match(text):
        notices.append(
            "Your answers are saved. You can come back any time and continue right here."
        )
        return result()
    if _BACK.match(text):
        plan = question_plan(answers, reask)
        idx = plan.index(field) if field in plan else 0
        if idx == 0:
            notices.append("This is the first question.")
        else:
            reask.insert(0, plan[idx - 1])
        return result()
    if m := _CORRECTION.match(text):
        target, _, new_value = m.group(1).partition(" to ")
        fields = [f for f in resolve_fields(target, answers) if f in question_plan(answers)]
        if not fields:
            notices.append(
                "I'm not sure which answer you'd like to change. You can say, for example, "
                "'change filing status', 'change date of birth' or 'change dependent 1'."
            )
            return result()
        if new_value.strip() and len(fields) == 1:
            try:
                value = parse_answer(fields[0], new_value.strip())
            except ParseError as err:
                notices.append(str(err))
                reask[:0] = [f for f in fields if f not in reask]
                return result()
            _store(answers, corrections, reask, fields[0], value, requests)
            _flag_documents(fields[0], value, escalations, text, decided)
            notices.append(f"Updated {_label(fields[0])}.")
            return result()
        reask[:0] = [f for f in fields if f not in reask]
        return result()

    # 4. Parse the answer to the current question.
    if field == "confirm":
        try:
            confirmed = parse_yes_no(text)
        except ParseError:
            fields = [f for f in resolve_fields(text, answers) if f in question_plan(answers)]
            if fields:
                reask[:0] = [f for f in fields if f not in reask]
            else:
                notices.append(
                    "Please reply 'yes' if everything is correct, or tell me what to change."
                )
            return result()
        if confirmed:
            _store(answers, corrections, reask, "confirm", True)
        else:
            notices.append("What would you like to change? For example 'change date of birth'.")
        return result()

    try:
        value = parse_answer(field, text)
    except ParseError as err:
        notices.append(str(err))
        return result()
    was_reask = field in reask and field in answers
    _store(answers, corrections, reask, field, value, requests)
    _flag_documents(field, value, escalations, text, decided)
    if was_reask:
        notices.append(f"Updated {_label(field)}.")
    return result()


def _flag_documents(
    field: str, value: Any, escalations: list[Escalation], text: str, decided=frozenset()
) -> None:
    if field != "documents":
        return
    for doc in value:
        if not document_in_scope(doc):
            reason = "an unlisted document" if doc["type"] == "other" else f"a {doc['type']}"
            _add_escalation(
                escalations, make_escalation("out_of_scope", reason, field, text), decided
            )


# --- Graph nodes ---------------------------------------------------------------------------


def _progress(answers: dict[str, Any]) -> dict[str, int]:
    plan = [f for f in question_plan(answers) if f != "confirm"]
    return {"answered": sum(1 for f in plan if f in answers), "total": len(plan)}


def _unsupported_reason(field: str) -> str:
    if field.startswith("document."):
        return (
            f"another agent asked for {field}, a value from a tax document; document details "
            "are entered by hand or read by Document Extraction, not collected by Intake"
        )
    if field.startswith("entered_amounts."):
        return f"another agent asked for {field}, an amount entered directly on a form line"
    return f"another agent asked for '{field}', which is not a question Intake can ask"


def _take_requests(
    state: IntakeState, answers: dict[str, Any], escalations: list[Escalation]
) -> tuple[list[str], dict[str, RequestStatus]]:
    """Sort the fields other agents asked for (``intake_reask``) into the ones to ask now.

    Nothing is dropped silently. A field Intake can't ask raises an ``unsupported_request``
    flag. A field that keeps coming back after the taxpayer has answered it raises a
    ``repeated_request`` flag instead of asking again, so a disagreement between agents
    reaches a person rather than going round in circles. Once a person decides on that flag,
    the field may be asked once more.
    """
    decided = _decided(state)
    requests = dict(state.get("intake_requests") or {})
    reask: list[str] = []
    for field in dict.fromkeys(canonical_field(f) for f in state.get("intake_reask") or []):
        prev = requests.get(field) or RequestStatus(requested=0, status="answered", cleared=0)
        # A field still pending from an earlier entry (e.g. the interview was held for a
        # person) is the same request, not a new one.
        count = prev["requested"] + (0 if prev["status"] == "pending" else 1)
        cleared = prev.get("cleared", 0)
        if field not in question_plan(answers, [field]):
            reason = _unsupported_reason(field)
            _add_escalation(
                escalations, make_escalation("unsupported_request", reason, field), decided
            )
            requests[field] = RequestStatus(requested=count, status="unsupported", cleared=cleared)
            continue
        if field in answers and count > MAX_REQUESTS_PER_FIELD:
            reason = f"another agent keeps asking for {_label(field)}, which the taxpayer has answered"
            flags = [
                e
                for e in escalations
                if e["type"] == "repeated_request" and e["field"] == field
            ]
            if flags and len(flags) > cleared and all(
                intake_escalation_id(e) in decided for e in flags
            ):
                count, cleared = 1, len(flags)  # a person has decided: ask once more
            else:
                if not any(intake_escalation_id(e) not in decided for e in flags):
                    _add_escalation(
                        escalations, make_escalation("repeated_request", reason, field), decided
                    )
                requests[field] = RequestStatus(requested=count, status="repeated", cleared=cleared)
                continue
        requests[field] = RequestStatus(requested=count, status="pending", cleared=cleared)
        reask.append(field)
    return reask, requests


def prepare(state: IntakeState) -> dict[str, Any]:
    """Normalize input: on (re-)entry, requested re-asks must be confirmed again."""
    answers = _prune(dict(state.get("intake_answers") or {}))
    escalations = list(state.get("intake_escalations") or [])
    reask, requests = _take_requests(state, answers, escalations)
    if reask:
        answers.pop("confirm", None)
    return {
        "intake_answers": answers,
        "intake_reask": reask,
        "intake_requests": requests,
        "intake_corrections": list(state.get("intake_corrections") or []),
        "intake_escalations": escalations,
        "intake_notice": state.get("intake_notice") or "",
        "intake_message": "",
        "intake_status": "in_progress",
        "intake_record": None,
        "needs_human_review": bool(escalations),
    }


def question_id(state: IntakeState, field: str) -> str:
    """Identifies one asking of one question, so a repeated submission can be recognised."""
    return f"{len(state.get('intake_transcript') or []) // 2 + 1}:{field}"


def ask(state: IntakeState) -> dict[str, Any]:
    """Ask the next question and wait for the answer (one question per super-step).

    Everything before ``interrupt()`` runs again when the node resumes, so it must stay free of
    side effects: it only reads state. The answer is applied after the interrupt, once.
    """
    field = next_question(state)
    answers = state.get("intake_answers") or {}
    payload = {
        "type": "intake_question",
        "session_id": state.get("session_id"),
        "question_id": question_id(state, field),
        "field": field,
        "question": question_text(field, answers),
        "notice": state.get("intake_notice") or "",
        "current_answer": display_value(field, answers[field])
        if field in answers and field != "confirm"
        else None,
        "progress": _progress(answers),
    }
    answer = interrupt(payload)
    return process_answer(state, field, answer)


def hold(state: IntakeState) -> dict[str, Any]:
    """Stop the interview for a flag a person must see first (exploitation, or the taxpayer
    asked for a person). Ends this run of Intake without asking anything else, so the
    Orchestrator sends the session to human review. When a person has decided on the flag,
    the next run of Intake picks up at the same question."""
    kinds = dict.fromkeys(e["type"] for e in open_blocking_escalations(state))
    return {
        "intake_status": "awaiting_human",
        "intake_record": None,
        "intake_message": " ".join(ESCALATION_NOTICES[k] for k in kinds),
        "intake_notice": "",
        "needs_human_review": True,
    }


def finalize(state: IntakeState) -> dict[str, Any]:
    answers = state["intake_answers"]
    escalations = list(state.get("intake_escalations") or [])
    decided = _decided(state)
    for reason, _fields in find_conflicts(answers):
        _add_escalation(
            escalations, make_escalation("conflicting_information", reason, None), decided
        )
    record = build_record(answers)
    return {
        "intake_status": "complete",
        "intake_record": record.model_dump(mode="json"),
        "intake_escalations": escalations,
        "intake_notice": "",
        "intake_message": "",
        "needs_human_review": bool(escalations),
    }


def _route(state: IntakeState) -> str:
    if open_blocking_escalations(state):
        return "intake_hold"
    return "intake_ask" if next_question(state) else "intake_finalize"


def build_intake_graph(checkpointer: Any = None):
    """Compile the Intake graph.

    Pass a checkpointer (``memory.make_checkpointer``) to run it standalone. Leave it as
    ``None`` to embed it as a subgraph: it then uses the parent graph's checkpointer.
    """
    builder = StateGraph(IntakeState, input_schema=IntakeInput, output_schema=IntakeOutput)
    builder.add_node("intake_prepare", prepare)
    builder.add_node("intake_ask", ask)
    builder.add_node("intake_hold", hold)
    builder.add_node("intake_finalize", finalize)
    builder.add_edge(START, "intake_prepare")
    nexts = ["intake_ask", "intake_hold", "intake_finalize"]
    builder.add_conditional_edges("intake_prepare", _route, nexts)
    builder.add_conditional_edges("intake_ask", _route, nexts)
    builder.add_edge("intake_hold", END)
    builder.add_edge("intake_finalize", END)
    return builder.compile(checkpointer=checkpointer, name="intake")


# --- Session driver (standalone use, CLI and tests) ----------------------------------------


def _turn(graph, session_id: str, *, resumed: bool = False) -> dict[str, Any]:
    snap = load_session(graph, session_id)
    if snap.pending:
        turn = {"status": "awaiting_answer", **snap.pending[0]}
        if resumed:
            p = turn["progress"]
            welcome = (
                f"Welcome back! Your answers are saved: you've answered {p['answered']} of "
                f"{p['total']} questions. Let's pick up where you left off."
            )
            turn["notice"] = " ".join(x for x in (welcome, turn.get("notice")) if x)
        return turn
    output = {k: snap.values.get(k) for k in OUTPUT_KEYS}
    return {
        # "awaiting_human": the interview stopped until a person decides on a flag.
        "status": "awaiting_human" if output["intake_status"] == "awaiting_human" else "complete",
        "session_id": session_id,
        "output": output,
    }


def start_or_resume(graph, session_id: str, initial: IntakeInput | None = None) -> dict[str, Any]:
    """Start a session, or return to the question an interrupted session was waiting on."""
    snap = load_session(graph, session_id)
    if snap.pending:
        return _turn(graph, session_id, resumed=True)
    if (
        snap.exists and snap.next_nodes
    ):  # stopped mid-step (e.g. a crash): carry on from the checkpoint
        graph.invoke(None, session_config(session_id))
        return _turn(graph, session_id)
    if snap.exists and not initial:
        return _turn(graph, session_id)
    graph.invoke({**(initial or {}), "session_id": session_id}, session_config(session_id))
    return _turn(graph, session_id)


def submit_answer(
    graph, session_id: str, answer: str, question_id: str | None = None
) -> dict[str, Any]:
    """Send the taxpayer's answer to the waiting question; returns the next question or the output.

    Pass the ``question_id`` the answer was given to. If the session has moved on (the same
    answer was sent twice, for example by a double click or a retried request), the answer is
    not applied to the next question: the current question comes back with status
    ``"stale_answer"``.
    """
    snap = load_session(graph, session_id)
    if not snap.pending:
        raise RuntimeError(f"session {session_id!r} is not waiting for an answer")
    if question_id is not None and snap.pending[0].get("question_id") != question_id:
        return {**_turn(graph, session_id), "status": "stale_answer"}
    graph.invoke(Command(resume=answer), session_config(session_id))
    return _turn(graph, session_id)


def request_corrections(
    graph, session_id: str, fields: list[str], reason: str = ""
) -> dict[str, Any]:
    """Re-open a completed session to ask specific questions again (e.g. after a document conflict)."""
    return start_or_resume(graph, session_id, {"intake_reask": fields, "intake_notice": reason})


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run a SilverFile intake interview in the terminal."
    )
    parser.add_argument(
        "--session", default="demo_taxpayer_001", help="session ID to start or resume"
    )
    parser.add_argument(
        "--db", default=None, help="SQLite file for saved sessions (default: data/sessions.sqlite)"
    )
    args = parser.parse_args(argv)

    from memory import DEFAULT_DB_PATH

    graph = build_intake_graph(make_checkpointer(args.db or DEFAULT_DB_PATH))
    turn = start_or_resume(graph, args.session)
    while turn["status"] == "awaiting_answer":
        if turn.get("notice"):
            print(f"\nSilverFile: {turn['notice']}")
        print(f"\nSilverFile: {turn['question']}")
        answer = input("You: ")
        if _PAUSE.match(answer):
            print(
                f"\nYour answers are saved. Resume with: python run.py intake --session {args.session}"
            )
            return
        turn = submit_answer(graph, args.session, answer, turn.get("question_id"))

    out = turn["output"]
    if turn["status"] == "awaiting_human":
        print(f"\nSilverFile: {out['intake_message']}")
        return
    print("\nThank you! Your intake is complete.")
    if out["needs_human_review"]:
        print("A Kushim preparer will follow up about:")
        for e in out["intake_escalations"]:
            print(f"  - {e['reason']}")


if __name__ == "__main__":
    main()
