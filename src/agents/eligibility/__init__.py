"""Eligibility & Rules: 1040-SR eligibility, filing status validation, line 12d box counts,
which schedules apply, and in-scope or out-of-scope decisions. Grounded in data/policy.md,
which every decision cites."""

from .agent import (
    EligibilityState,
    add_eligibility_node,
    build_eligibility_graph,
    eligibility_node,
)
from .corpus import cite, citation_records, load_policy
from .facts import QUESTIONS, EligibilityFacts, HumanResolution, facts_from_state
from .rules_engine import RESOLVABLE_CODES, EligibilityDecision, evaluate

__all__ = [
    "QUESTIONS",
    "RESOLVABLE_CODES",
    "EligibilityDecision",
    "EligibilityFacts",
    "EligibilityState",
    "HumanResolution",
    "add_eligibility_node",
    "build_eligibility_graph",
    "cite",
    "citation_records",
    "eligibility_node",
    "evaluate",
    "facts_from_state",
    "load_policy",
]
