"""Sample data and helpers shared by the orchestrator tests. Not a test itself.

A whole session for the full graph: the interview already answered, the two demo W-2s read by
the offline fixture reader, and the other forms typed in. Every SSN is in the 900-999 test range.

Intake's answers are complete (including the taxpayer's "yes" to the summary), so Intake finishes
without asking anything. ``eligibility_facts`` supplies the answers Eligibility & Rules needs that
Intake doesn't ask yet.
"""

import copy
from pathlib import Path

from memory import load_session, session_config

from ..computation.scenarios import mfj_return

ROOT = Path(__file__).resolve().parents[2]

INTAKE_ANSWERS = {
    "filing_status": "married_filing_jointly",
    "taxpayer_ssn": "900-12-3456",
    "taxpayer_dob": "1958-03-02",
    "taxpayer_blind": False,
    "spouse_ssn": "901-22-3333",
    "spouse_dob": "1960-07-19",
    "spouse_blind": False,
    "has_dependents": False,
    "documents": [
        {"type": "W-2", "count": 2},
        {"type": "SSA-1099", "count": 2},
        {"type": "1099-INT", "count": 1},
        {"type": "1099-R", "count": 1},
    ],
    "has_estimated_payments": True,
    "estimated_payment_count": 2,
    "estimated_payment.1.amount": "1000.00",
    "estimated_payment.2.amount": "1000.00",
    "confirm": True,
}
ELIGIBILITY_FACTS = {
    "spouse_died_in_2025": False,
    "can_be_claimed_as_dependent": False,
    "spouse_can_be_claimed_as_dependent": False,
    "dual_status_alien": False,
}


def session(session_id: str = "session_1") -> dict:
    """The clean session: married filing jointly, both 65 or older, two W-2s."""
    return {
        "session_id": session_id,
        "intake_answers": copy.deepcopy(INTAKE_ANSWERS),
        "eligibility_facts": dict(ELIGIBILITY_FACTS),
        "documents": [
            {"document_id": "w2-1", "document_type": "W-2", "path": str(ROOT / "demo/w2_image_1.jpg")},
            {"document_id": "w2-2", "document_type": "W-2", "path": str(ROOT / "demo/w2_image_2.jpg")},
        ],
        "entered_documents": mfj_return()["entered_documents"],
    }


def run(graph, state: dict):
    """Start a session and return a snapshot of where it stopped."""
    graph.invoke(state, session_config(state["session_id"]))
    return load_session(graph, state["session_id"])


def lines(values: dict) -> dict:
    return {line: rec["value"] for line, rec in (values.get("draft_return") or {}).get("lines", {}).items()}


def decide(packet: dict, decision: str, **extra) -> dict:
    """A preparer's answer giving every open issue in the packet the same decision."""
    return {"decided_by": "preparer_test",
            "decisions": [{"escalation_id": i["escalation_id"], "decision": decision, **extra}
                          for i in packet["issues"]]}
