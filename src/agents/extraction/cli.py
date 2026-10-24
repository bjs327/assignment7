"""Try the extraction agent on one or more W-2 images.

    python run.py extract demo/w2_image_1.jpg demo/w2_image_2.jpg
    python run.py extract demo/w2_image_1.jpg --offline   # no API key needed

Prints the extracted records, escalations and trace as JSON. SSNs are masked.
"""

from __future__ import annotations

import argparse
import json

from dotenv import load_dotenv

from .agent import build_extraction_graph
from .extractor import FixtureW2Extractor, LLMVisionW2Extractor


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="run.py extract", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("images", nargs="+", help="W-2 image files (JPG/PNG)")
    parser.add_argument("--offline", action="store_true",
                        help="Use the fixture ground-truth JSON instead of calling a model")
    args = parser.parse_args(argv)

    extractor = FixtureW2Extractor() if args.offline else LLMVisionW2Extractor()
    graph = build_extraction_graph(extractor)
    documents = [{"document_id": f"w2-{i}", "document_type": "W-2", "path": p}
                 for i, p in enumerate(args.images, start=1)]
    state = graph.invoke({"documents": documents})
    print(json.dumps({k: state.get(k) for k in ("extracted_documents", "escalations", "trace")}, indent=2))


if __name__ == "__main__":
    main()
