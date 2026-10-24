"""The extraction fan-out writes concurrently; the merged state must not depend on arrival order."""

import itertools

import pytest

pytest.importorskip("langgraph")

from agents.extraction.agent import merge_escalations, merge_extractions  # noqa: E402


def _r(doc_id, attempt=1, status="extracted"):
    return {"document_id": doc_id, "attempt": attempt, "status": status}


def test_merge_is_order_independent():
    results = [_r("w2-3"), _r("w2-1"), _r("w2-2")]
    outcomes = set()
    for perm in itertools.permutations(results):
        state = []
        for item in perm:  # each branch's write arrives separately
            state = merge_extractions(state, [item])
        outcomes.add(tuple(r["document_id"] for r in state))
    assert outcomes == {("w2-1", "w2-2", "w2-3")}


def test_batched_write_matches_sequential_writes():
    batch = merge_extractions([], [_r("b"), _r("a")])
    seq = merge_extractions(merge_extractions([], [_r("b")]), [_r("a")])
    assert batch == seq


def test_reextraction_replaces_earlier_attempt_whatever_the_order():
    old, new = _r("w2-1", 1, "needs_review"), _r("w2-1", 2, "extracted")
    assert merge_extractions([old], [new]) == [new]
    assert merge_extractions([new], [old]) == [new]  # a stale write can't win


def test_same_attempt_later_write_wins():
    first, annotated = _r("w2-1"), {**_r("w2-1"), "status": "needs_review"}
    assert merge_extractions([first], [annotated]) == [annotated]


def test_escalations_deduplicated():
    e = {"source_agent": "document_extraction", "document_id": "w2-1", "code": "non_test_ssn", "attempt": 1}
    assert merge_escalations([e], [dict(e)]) == [e]
    assert len(merge_escalations([e], [{**e, "attempt": 2}])) == 2
