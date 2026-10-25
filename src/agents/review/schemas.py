"""Data shapes for the Review / Audit agent.

Review reads the draft return and writes a ReviewReport. It never edits the
return: it can only say what is wrong and which agent should fix it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

# A review->revise loop may run at most this many reviews before a human is
# called in (so at most MAX_REVIEW_ITERATIONS - 1 rounds of revision).
MAX_REVIEW_ITERATIONS = 3

BLOCKER = "blocker"  # the return cannot go to a human reviewer as it stands
WARNING = "warning"  # worth a human's attention, does not stop the return

# Who should act on a finding. "human" means escalate (LangGraph interrupt).
AGENTS = ("intake", "extraction", "eligibility", "computation")
HUMAN = "human"

# Order in which sent-back work should be redone: each agent's output feeds the
# next, so fixing them in this order avoids a second, avoidable review loop.
REVISION_ORDER = ("intake", "extraction", "eligibility", "computation")

APPROVED = "approved"
REVISE = "revise"
ESCALATE = "escalate"


@dataclass(frozen=True)
class Finding:
    check: str  # stable check id, e.g. "R05-arithmetic"
    line: str | None  # form line the finding is about, if any
    severity: str  # BLOCKER or WARNING
    route_to: str  # one of AGENTS, or HUMAN
    message: str  # plain English, safe to show on the dashboard
    expected: str | None = None
    actual: str | None = None
    document_id: str | None = None  # set when the fix is "read this document again"

    @property
    def id(self) -> str:
        return f"{self.check}:{self.line or '-'}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["id"] = self.id
        return d


@dataclass
class ReviewReport:
    iteration: int
    verdict: str  # APPROVED, REVISE or ESCALATE
    findings: list[Finding] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)
    revision_requests: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    reextract_document_ids: list[str] = field(default_factory=list)
    fingerprint: str = ""
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["findings"] = [f.to_dict() for f in self.findings]
        return d


def fingerprint(findings: list[Finding]) -> str:
    """Stable hash of a set of blocker findings.

    Two reviews with the same fingerprint found the same problems in the same
    places with the same numbers, which means the last revision changed
    nothing useful. That is the loop guard's signal.
    """
    key = sorted((f.check, f.line or "", f.expected or "", f.actual or "") for f in findings)
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16]
