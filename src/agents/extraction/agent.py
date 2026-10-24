"""Document Extraction agent as LangGraph nodes.

Graph shape (the spec's "parallel branch with an aggregation step"):

        START
          |  route_documents  -> one Send per W-2 (parallel fan-out)
          v
   extract_document  x N   (each branch reads ONE document)
          |
          v
   aggregate_extractions   (runs once, after every branch finishes)
          |
         END

Concurrent branches all write to ``extracted_documents``. The
``merge_extractions`` reducer makes that safe: the result is keyed by
document id and sorted, so arrival order never changes the outcome.

Constraint from the spec: this agent "must not decide what a value means for
the return". It produces typed W-2 records with provenance and issues. It does
not sum wages into line 1z, and it does not raise interrupts itself; it emits
escalation records and the Orchestrator decides when to call ``interrupt()``.
"""

from __future__ import annotations

import operator
from datetime import datetime, timezone
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from security import redact_ssns

from .extractor import FixtureW2Extractor, LLMVisionW2Extractor, W2Extractor, load_image
from .schemas import ExtractionIssue, ExtractionResult
from .validation import build_w2_result

AGENT_NAME = "document_extraction"
EXTRACTABLE_TYPES = {"W-2"}
MAX_READ_ATTEMPTS = 2  # one retry if the model call or its output fails


# --------------------------------------------------------------------------
# Reducers (safe under concurrent writes)
# --------------------------------------------------------------------------


def merge_extractions(left: list[dict] | None, right: list[dict] | None) -> list[dict]:
    """Merge extraction results from parallel branches.

    * Keyed by ``document_id`` so a document appears once.
    * A higher ``attempt`` (a re-extraction requested by Review) wins.
    * At the same attempt, the later write wins (lets the aggregation step
      annotate results).
    * Output is sorted by ``document_id``, so the merged state is identical
      whatever order the branches finished in.
    """
    merged: dict[str, dict] = {}
    for item in (left or []) + (right or []):
        key = item["document_id"]
        current = merged.get(key)
        if current is None or item.get("attempt", 1) >= current.get("attempt", 1):
            merged[key] = item
    return [merged[k] for k in sorted(merged)]


def merge_escalations(left: list[dict] | None, right: list[dict] | None) -> list[dict]:
    """Append escalations, dropping exact repeats (same agent, document, code, attempt)."""
    out: list[dict] = []
    seen: set[tuple] = set()
    for item in (left or []) + (right or []):
        key = (item.get("source_agent"), item.get("document_id"), item.get("code"), item.get("attempt"))
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


class ExtractionState(TypedDict, total=False):
    # Input, written by Intake's document inventory:
    #   [{"document_id": "w2-1", "document_type": "W-2", "path": "demo/w2_image_1.jpg"}, ...]
    documents: list[dict]
    # Optional: document ids the Review agent wants read again.
    reextract_document_ids: list[str]
    # Outputs
    extracted_documents: Annotated[list[dict], merge_extractions]
    escalations: Annotated[list[dict], merge_escalations]
    trace: Annotated[list[dict], operator.add]
    extraction_complete: bool


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_document_type(raw: str | None) -> str:
    text = (raw or "").upper().replace(" ", "").replace("_", "-")
    return "W-2" if text in {"W-2", "W2"} else (raw or "unknown")


def _escalation(result: dict, issue: dict) -> dict:
    return {
        "source_agent": AGENT_NAME,
        "document_id": result["document_id"],
        "code": issue["code"],
        "reason": issue["message"],
        "attempt": result.get("attempt", 1),
        "created_at": _now(),
    }


def _trace(event: str, **data: Any) -> dict:
    return {"agent": AGENT_NAME, "event": event, "at": _now(), **data}


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------


def route_documents(state: ExtractionState) -> list[Send] | str:
    """Conditional edge: fan out one branch per W-2 still needing extraction."""
    done = {r["document_id"]: r for r in state.get("extracted_documents") or []}
    redo = set(state.get("reextract_document_ids") or [])
    sends = []
    for doc in state.get("documents") or []:
        if normalize_document_type(doc.get("document_type")) not in EXTRACTABLE_TYPES:
            continue
        doc_id = doc["document_id"]
        if doc_id in done and doc_id not in redo:
            continue
        attempt = done[doc_id].get("attempt", 1) + 1 if doc_id in done else 1
        sends.append(Send("extract_document", {"document": doc, "attempt": attempt}))
    return sends or "aggregate_extractions"


def make_extract_node(extractor: W2Extractor) -> Callable[[dict], dict]:
    def extract_document(payload: dict) -> dict:
        doc = payload["document"]
        attempt = payload.get("attempt", 1)
        doc_id = doc["document_id"]

        def failed(code: str, message: str, **extra) -> dict:
            result = ExtractionResult(
                document_id=doc_id, document_type="W-2", status="failed", attempt=attempt,
                issues=[ExtractionIssue(code=code, severity="escalate", message=message)],
                extracted_at=_now(), **extra,
            ).model_dump(mode="json")
            return {
                "extracted_documents": [result],
                "escalations": [_escalation(result, result["issues"][0])],
                "trace": [_trace("failed", document_id=doc_id, code=code, attempt=attempt)],
            }

        try:
            image = load_image(doc["path"])
        except (OSError, ValueError, KeyError) as exc:
            return failed("file_unreadable", "I couldn't open this document file. A person should check it.",
                          file_name=str(doc.get("path")))

        reading, last_error = None, None
        for _ in range(MAX_READ_ATTEMPTS):
            try:
                reading = extractor.read(image)
                break
            except Exception as exc:  # model/network/schema errors
                last_error = redact_ssns(f"{type(exc).__name__}: {exc}")[:300]
        if reading is None:
            out = failed("extraction_failed",
                         "I wasn't able to read this W-2 automatically. A person should enter it by hand.",
                         file_name=image.file_name, file_sha256=image.sha256)
            out["trace"][0]["error"] = last_error
            return out

        result = build_w2_result(
            document=doc, reading=reading, file_name=image.file_name, file_sha256=image.sha256,
            method=extractor.name, extracted_at=_now(), attempt=attempt,
        ).model_dump(mode="json")

        escalations = [_escalation(result, i) for i in result["issues"] if i["severity"] == "escalate"]
        return {
            "extracted_documents": [result],
            "escalations": escalations,
            "trace": [_trace("extracted", document_id=doc_id, status=result["status"],
                             fields=sorted(result["fields"]), attempt=attempt)],
        }

    return extract_document


def aggregate_extractions(state: ExtractionState) -> dict:
    """Runs once after all branches: cross-document checks + non-W-2 bookkeeping."""
    results = {r["document_id"]: dict(r) for r in state.get("extracted_documents") or []}
    updates: list[dict] = []
    escalations: list[dict] = []

    # Documents this agent doesn't read are entered by the user (spec 3.1).
    for doc in state.get("documents") or []:
        doc_type = normalize_document_type(doc.get("document_type"))
        if doc_type not in EXTRACTABLE_TYPES and doc["document_id"] not in results:
            updates.append(ExtractionResult(
                document_id=doc["document_id"], document_type=doc_type, status="manual_entry_required",
                issues=[ExtractionIssue(code="manual_entry", severity="info",
                                        message=f"{doc_type} amounts will be entered directly.")],
                extracted_at=_now(),
            ).model_dump(mode="json"))

    # The same file uploaded twice would double-count wages: flag it.
    by_hash: dict[str, list[str]] = {}
    for r in results.values():
        if r.get("file_sha256"):
            by_hash.setdefault(r["file_sha256"], []).append(r["document_id"])
    for ids in by_hash.values():
        if len(ids) < 2:
            continue
        for doc_id in sorted(ids)[1:]:
            r = results[doc_id]
            if any(i["code"] == "duplicate_document" for i in r["issues"]):
                continue
            issue = ExtractionIssue(
                code="duplicate_document", severity="escalate",
                message=f"This looks like the same file as {sorted(ids)[0]}. It may have been uploaded twice.",
            ).model_dump(mode="json")
            r["issues"] = r["issues"] + [issue]
            r["status"] = "needs_review"
            updates.append(r)
            escalations.append(_escalation(r, issue))

    final = {**results, **{u["document_id"]: u for u in updates}}
    summary = {status: sum(1 for r in final.values() if r["status"] == status)
               for status in ("extracted", "needs_review", "failed", "manual_entry_required")}
    return {
        "extracted_documents": updates,
        "escalations": escalations,
        "reextract_document_ids": [],
        "extraction_complete": True,
        "trace": [_trace("aggregated", **summary)],
    }


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------


def add_extraction_nodes(builder: StateGraph, extractor: W2Extractor | None = None,
                         entry: str = START, exit_to: str = END) -> None:
    """Wire the extraction nodes into an existing graph (e.g. the team's main graph).

    ``entry``: node the fan-out starts after (START when standalone, or the
    orchestrator node). ``exit_to``: where control goes after aggregation.
    The parent state must include the ExtractionState keys and reducers.
    """
    builder.add_node("extract_document", make_extract_node(extractor or LLMVisionW2Extractor()))
    builder.add_node("aggregate_extractions", aggregate_extractions)
    builder.add_conditional_edges(entry, route_documents, ["extract_document", "aggregate_extractions"])
    builder.add_edge("extract_document", "aggregate_extractions")
    builder.add_edge("aggregate_extractions", exit_to)


def build_extraction_graph(extractor: W2Extractor | None = None, checkpointer=None):
    """Standalone compiled graph. Can also be added to a parent graph as a subgraph node."""
    builder = StateGraph(ExtractionState)
    add_extraction_nodes(builder, extractor)
    return builder.compile(checkpointer=checkpointer)


__all__ = [
    "ExtractionState",
    "FixtureW2Extractor",
    "LLMVisionW2Extractor",
    "add_extraction_nodes",
    "aggregate_extractions",
    "build_extraction_graph",
    "labeled_amounts",
    "make_extract_node",
    "merge_escalations",
    "merge_extractions",
    "route_documents",
]


def labeled_amounts(extracted_documents: list[dict], box: str) -> list[dict]:
    """Every extracted value for one W-2 box, as tax_math ``{label, amount}`` items.

    Example: ``labeled_amounts(state["extracted_documents"], "1")`` gives the W-2
    box 1 amounts the Computation agent passes to ``sum_amounts``. Only results
    with status "extracted" are included; documents still needing review are
    left out so they can't reach the return unchecked. Which line a box feeds
    is the Computation agent's decision, not this one's.
    """
    out = []
    for result in extracted_documents:
        field = result.get("fields", {}).get(f"box{box}")
        if result.get("status") == "extracted" and field:
            out.append({"label": field["source_label"], "amount": field["value"]})
    return out
