"""The state reducer must merge concurrent writes from the parallel extraction branch
correctly, whatever order they arrive in (specification section 4.1).

What is tested:
- Extraction results from parallel branches merge into the same list in every possible
  arrival order.
- Escalations from several agents merge the same way in every order, and one reported twice
  is kept once.
- An escalation a preparer resolved stays resolved, but opens again if the agent raises the
  same problem again.
- The full graph produces the same extracted W-2s whichever order the documents are listed in.

Owner of src/state.py: Verena.
"""

from itertools import permutations

from agents.computation import mcp_tool_caller
from agents.extraction import FixtureW2Extractor
from graph import build_graph
from memory import load_session, make_checkpointer, session_config
from state import merge_escalation_records, merge_extractions

from .orchestrator.scenarios import session


def _result(doc_id: str, attempt: int = 1, status: str = "extracted") -> dict:
    return {"document_id": doc_id, "document_type": "W-2", "status": status, "attempt": attempt}


def _escalation(agent: str, code: str, doc: str | None = None, attempt: int = 1, **extra) -> dict:
    return {"source_agent": agent, "document_id": doc, "code": code, "reason": code, "attempt": attempt, **extra}


def _merge_all(reducer, writes):
    merged = []
    for write in writes:
        merged = reducer(merged, write)
    return merged


def test_parallel_extraction_results_merge_in_any_order():
    writes = [[_result("w2-3")], [_result("w2-1")], [_result("w2-2", status="needs_review")]]
    results = {tuple(map(str, _merge_all(merge_extractions, order))) for order in permutations(writes)}
    assert len(results) == 1
    assert [r["document_id"] for r in _merge_all(merge_extractions, writes)] == ["w2-1", "w2-2", "w2-3"]


def test_parallel_escalations_merge_in_any_order():
    writes = [
        [_escalation("document_extraction", "unreadable_box", "w2-2")],
        [_escalation("document_extraction", "non_test_ssn", "w2-1")],
        [_escalation("eligibility_rules", "spouse_died_in_tax_year")],
        [_escalation("document_extraction", "unreadable_box", "w2-2")],  # the same one again
    ]
    results = {tuple(map(str, _merge_all(merge_escalation_records, order))) for order in permutations(writes)}
    assert len(results) == 1
    assert len(_merge_all(merge_escalation_records, writes)) == 3


def test_a_resolved_escalation_reopens_if_the_agent_raises_it_again():
    raised = _escalation("eligibility_rules", "document_count_mismatch", "W-2")
    resolved = {**raised, "resolved": True, "resolution": {"decision": "re_ask"}}
    merged = merge_escalation_records([raised], [resolved])
    assert merged == [resolved]
    merged = merge_escalation_records(merged, [raised])  # the problem is still there
    assert merged == [raised] and "resolved" not in merged[0]


def test_the_full_graph_reads_documents_the_same_whatever_order_they_arrive_in():
    call = mcp_tool_caller()
    outcomes = []
    for n, order in enumerate([[0, 1], [1, 0]]):
        graph = build_graph(make_checkpointer(), extractor=FixtureW2Extractor(),
                            computation_caller=call, review_caller=call)
        state = session(f"order_{n}")
        state["documents"] = [state["documents"][i] for i in order]
        graph.invoke(state, session_config(state["session_id"]))
        values = load_session(graph, state["session_id"]).values
        outcomes.append([(r["document_id"], r["fields"]["box1"]["value"]) for r in values["extracted_documents"]])
    assert outcomes[0] == outcomes[1] == [("w2-1", "18500.00"), ("w2-2", "6200.00")]
