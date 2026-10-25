"""The review->revise loop: bound, loop guard, and what the orchestrator sees."""
import copy

from agents.review import (APPROVED, ESCALATE, MAX_REVIEW_ITERATIONS, REVISE, detect_handoff_cycle,
                           make_review_node, next_hops, run_review)
from conftest import clean_state, fake_tool_caller


def apply(state, update):
    """Merge a node's update the way LangGraph would (lists with add reducers append)."""
    s = copy.deepcopy(state)
    for k, v in update.items():
        if k in ("review_fingerprints", "review_history", "escalations"):
            s[k] = s.get(k, []) + v
        else:
            s[k] = v
    return s


def test_loop_ends_approved_when_the_fix_works():
    node = make_review_node(fake_tool_caller)
    s = clean_state()
    s["draft_return"]["lines"]["16"]["value"] += 5
    s = apply(s, node(s))
    assert s["review_report"]["verdict"] == REVISE
    s["draft_return"] = clean_state()["draft_return"]   # computation fixed it
    s = apply(s, node(s))
    assert s["review_report"]["verdict"] == APPROVED and s["review_iteration"] == 2


def test_iteration_bound_stops_a_loop_that_keeps_finding_new_problems():
    """Each round the 'fix' breaks something different, so the fingerprint guard
    does not fire; only the iteration bound can stop it."""
    node = make_review_node(fake_tool_caller)
    s = clean_state()
    lines = ["16", "10", "26", "6a", "25a"]
    verdicts = []
    for i in range(MAX_REVIEW_ITERATIONS + 2):
        s["draft_return"] = clean_state()["draft_return"]
        s["draft_return"]["lines"][lines[i]]["value"] += 7
        s = apply(s, node(s))
        verdicts.append(s["review_report"]["verdict"])
        if verdicts[-1] == ESCALATE:
            break
    assert verdicts[:-1] == [REVISE] * (MAX_REVIEW_ITERATIONS - 1)
    assert verdicts[-1] == ESCALATE
    assert len(verdicts) == MAX_REVIEW_ITERATIONS
    assert s["escalations"][-1]["code"] == "review_iteration_limit"


def test_loop_guard_breaks_a_revision_that_changes_nothing():
    """Computation 'revises' but returns the same wrong number: stop after the second review."""
    node = make_review_node(fake_tool_caller)
    s = clean_state()
    s["draft_return"]["lines"]["16"]["value"] += 5
    s = apply(s, node(s))
    assert s["review_report"]["verdict"] == REVISE
    s = apply(s, node(s))                      # nothing changed
    assert s["review_report"]["verdict"] == ESCALATE
    assert s["review_iteration"] == 2 < MAX_REVIEW_ITERATIONS
    assert s["escalations"][-1]["code"] == "review_loop_detected"


def test_escalation_shape_matches_document_extraction():
    s = clean_state()
    s["draft_return"]["scope_facts"]["itemizing"] = True
    _, esc = run_review(s, fake_tool_caller)
    assert set(esc[0]) == {"source_agent", "document_id", "code", "reason", "attempt", "created_at"}
    assert esc[0]["source_agent"] == "review"


def test_review_alone_never_writes_to_the_return_or_other_agents_state():
    node = make_review_node(fake_tool_caller)
    s = clean_state()
    s["draft_return"]["lines"]["16"]["value"] += 5
    update = node(s)
    assert "draft_return" not in update and "extracted_documents" not in update


def test_next_hops_follow_dependency_order():
    report = {"verdict": REVISE, "revision_requests": {"computation": [1], "intake": [2], "eligibility": [3]}}
    assert next_hops(report) == ["intake", "eligibility", "computation"]
    assert next_hops({"verdict": APPROVED}) == ["done"]
    assert next_hops({"verdict": ESCALATE}) == ["end_for_human"]


def test_detect_handoff_cycle():
    assert detect_handoff_cycle(["intake", "computation", "review", "computation", "review", "computation", "review"], 3) == ["computation", "review"]
    assert detect_handoff_cycle(["a", "a", "a"], 3) == ["a"]
    assert detect_handoff_cycle(["intake", "extraction", "computation", "review"], 3) is None
    assert detect_handoff_cycle([], 3) is None
