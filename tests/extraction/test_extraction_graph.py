"""End-to-end tests of the extraction subgraph, using the offline fixture reader (no API key)."""

import json
import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))

from generate_fixtures import generate  # noqa: E402

from agents.extraction import FixtureW2Extractor, build_extraction_graph, labeled_amounts  # noqa: E402
from agents.extraction.schemas import W2Reading  # noqa: E402


@pytest.fixture()
def fixtures(tmp_path):
    generate(tmp_path)
    return tmp_path


def _doc(doc_id, path, doc_type="W-2"):
    return {"document_id": doc_id, "document_type": doc_type, "path": str(path)}


def _by_id(state):
    return {r["document_id"]: r for r in state["extracted_documents"]}


def test_two_w2s_extract_in_parallel_and_aggregate(fixtures):
    graph = build_extraction_graph(FixtureW2Extractor())
    state = graph.invoke({"documents": [
        _doc("w2-2", fixtures / "demo/w2_image_2.jpg"),
        _doc("w2-1", fixtures / "demo/w2_image_1.jpg"),
        _doc("int-1", "n/a", "1099-INT"),
    ]})
    results = _by_id(state)
    assert [r["document_id"] for r in state["extracted_documents"]] == ["int-1", "w2-1", "w2-2"]
    assert results["w2-1"]["fields"]["box1"]["value"] == "18500.00"
    assert results["w2-2"]["fields"]["box2"]["value"] == "310.00"
    assert results["w2-2"]["fields"]["box1"]["provenance"]["file_name"] == "w2_image_2.jpg"
    assert results["int-1"]["status"] == "manual_entry_required"
    assert state["escalations"] == [] and state["extraction_complete"] is True
    assert any(t["event"] == "aggregated" for t in state["trace"])
    assert labeled_amounts(state["extracted_documents"], "1") == [
        {"label": "W-2 w2-1 (Maple Grove Public Library) box 1", "amount": "18500.00"},
        {"label": "W-2 w2-2 (Lakeside Garden Center LLC) box 1", "amount": "6200.00"},
    ]


def test_non_test_ssn_escalates_and_never_appears_in_state(fixtures):
    graph = build_extraction_graph(FixtureW2Extractor())
    state = graph.invoke({"documents": [_doc("w2-x", fixtures / "data/fixtures/w2_non_test_ssn.jpg")]})
    assert _by_id(state)["w2-x"]["status"] == "needs_review"
    assert labeled_amounts(state["extracted_documents"], "1") == []  # held back until reviewed
    assert [e["code"] for e in state["escalations"]] == ["non_test_ssn"]
    assert "123-45-6789" not in json.dumps(state)


def test_prior_year_escalates(fixtures):
    graph = build_extraction_graph(FixtureW2Extractor())
    state = graph.invoke({"documents": [_doc("w2-y", fixtures / "data/fixtures/w2_prior_year.jpg")]})
    assert [e["code"] for e in state["escalations"]] == ["wrong_tax_year"]


def test_duplicate_upload_is_flagged(fixtures):
    copy = fixtures / "demo/w2_copy.jpg"
    shutil.copy(fixtures / "demo/w2_image_1.jpg", copy)
    shutil.copy(fixtures / "demo/w2_image_1.json", copy.with_suffix(".json"))
    graph = build_extraction_graph(FixtureW2Extractor())
    state = graph.invoke({"documents": [_doc("w2-1", fixtures / "demo/w2_image_1.jpg"), _doc("w2-9", copy)]})
    results = _by_id(state)
    assert results["w2-1"]["status"] == "extracted"
    assert results["w2-9"]["status"] == "needs_review"
    assert [e["code"] for e in state["escalations"]] == ["duplicate_document"]


class FlakyExtractor:
    """Fails a set number of times before succeeding."""

    name = "flaky"

    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    def read(self, image):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("model said 123-45-6789 is invalid JSON")
        return W2Reading.model_validate(json.loads(image.path.with_suffix(".json").read_text())["reading"])


def test_one_bad_model_response_is_retried(fixtures):
    ex = FlakyExtractor(failures=1)
    state = build_extraction_graph(ex).invoke({"documents": [_doc("w2-1", fixtures / "demo/w2_image_1.jpg")]})
    assert ex.calls == 2 and _by_id(state)["w2-1"]["status"] == "extracted"


def test_persistent_failure_escalates_with_redacted_error(fixtures):
    ex = FlakyExtractor(failures=99)
    state = build_extraction_graph(ex).invoke({"documents": [_doc("w2-1", fixtures / "demo/w2_image_1.jpg")]})
    assert _by_id(state)["w2-1"]["status"] == "failed"
    assert [e["code"] for e in state["escalations"]] == ["extraction_failed"]
    assert "123-45-6789" not in json.dumps(state)


def test_missing_file_fails_gracefully(fixtures):
    state = build_extraction_graph(FixtureW2Extractor()).invoke(
        {"documents": [_doc("w2-1", fixtures / "nope.jpg")]})
    assert _by_id(state)["w2-1"]["status"] == "failed"
    assert state["escalations"][0]["code"] == "file_unreadable"


def test_review_can_request_reextraction(fixtures):
    graph = build_extraction_graph(FixtureW2Extractor())
    docs = [_doc("w2-1", fixtures / "demo/w2_image_1.jpg"), _doc("w2-2", fixtures / "demo/w2_image_2.jpg")]
    first = graph.invoke({"documents": docs})
    second = graph.invoke({**first, "reextract_document_ids": ["w2-2"]})
    results = _by_id(second)
    assert results["w2-1"]["attempt"] == 1 and results["w2-2"]["attempt"] == 2
    assert second["reextract_document_ids"] == []
