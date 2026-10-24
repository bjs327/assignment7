"""Document Extraction agent: W-2 images -> typed records with provenance."""

from .agent import (
    ExtractionState,
    add_extraction_nodes,
    aggregate_extractions,
    build_extraction_graph,
    labeled_amounts,
    make_extract_node,
    merge_escalations,
    merge_extractions,
    route_documents,
)
from .extractor import FixtureW2Extractor, LLMVisionW2Extractor, load_image
from .schemas import ExtractionResult, W2Reading
from .validation import build_w2_result, parse_amount

__all__ = [
    "ExtractionResult",
    "ExtractionState",
    "FixtureW2Extractor",
    "LLMVisionW2Extractor",
    "W2Reading",
    "add_extraction_nodes",
    "aggregate_extractions",
    "build_extraction_graph",
    "build_w2_result",
    "labeled_amounts",
    "load_image",
    "make_extract_node",
    "merge_escalations",
    "merge_extractions",
    "parse_amount",
    "route_documents",
]
