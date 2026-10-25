"""Loop guards.

Two different loops can go wrong in this system, and each has its own guard.

1. Review -> revise -> Review. Guarded in node.py by an iteration bound
   (MAX_REVIEW_ITERATIONS) and by a fingerprint of the blocking findings: if
   a revision leaves the findings exactly as they were, the loop is not making
   progress, so it stops and escalates without waiting for the bound.

2. Agents handing work to each other forever (A -> B -> A -> B ...), for any
   reason. detect_handoff_cycle looks at the trace of which agent ran and
   reports a repeating pattern. The Orchestrator should call it before every
   routing decision.
"""
from __future__ import annotations


def detect_handoff_cycle(trace: list[str], max_repeats: int = 3) -> list[str] | None:
    """Return the repeating pattern if the trace ends in one, else None.

    A pattern of length 1 to 4 that repeats `max_repeats` times in a row at the
    end of the trace counts. ["intake", "review", "computation", "review",
    "computation", "review", "computation", "review"] with max_repeats=3 ends in
    ["computation", "review"] three times, so that pair is returned.
    """
    n = len(trace)
    for size in range(1, 5):
        need = size * max_repeats
        if n < need:
            continue
        tail = trace[-need:]
        pattern = tail[:size]
        if all(tail[i] == pattern[i % size] for i in range(need)):
            return pattern
    return None
