"""Run the Eligibility & Rules agent on a state file (JSON) and print its decision.

    python run.py eligibility data/fixtures/eligibility_clean_mfj.json
    python run.py eligibility data/fixtures/eligibility_spouse_died.json
    python run.py eligibility data/fixtures/eligibility_spouse_died.json --resolve married_filing_jointly

The file holds the state keys the agent reads: intake_record, intake_answers,
extracted_documents, entered_documents, human_resolutions. ``--resolve STATUS`` simulates a
preparer resolving a filing-status escalation by choosing the status. No API key needed.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .agent import build_eligibility_graph


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="run.py eligibility", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("state_file", type=Path, help="JSON file with the agent's input state")
    parser.add_argument("--resolve", metavar="STATUS",
                        help="resolve open filing-status escalations by choosing this filing status")
    parser.add_argument("--full", action="store_true", help="print every finding with its citations")
    args = parser.parse_args(argv)

    state = json.loads(args.state_file.read_text(encoding="utf-8"))
    graph = build_eligibility_graph()
    result = graph.invoke(state)

    if args.resolve:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        resolutions = [
            {"code": e["code"], "filing_status": args.resolve, "resolved_by": "cli-preparer",
             "resolved_at": now, "note": "Resolved from the command line."}
            for e in result["eligibility"]["escalations"]
            if e["category"] == "filing_status_ambiguity"
        ]
        result = graph.invoke({**result, "human_resolutions": state.get("human_resolutions", []) + resolutions})

    decision = result["eligibility"]
    if not args.full:
        decision = {k: v for k, v in decision.items() if k != "findings"} | {
            "findings": [f"{f['topic']}: {f['value']} ({', '.join(f['rule_ids'])}; {f['decided_by']})"
                         for f in decision["findings"]]
        }
    print(json.dumps({"eligibility": decision, "eligibility_reask": result["eligibility_reask"],
                      "escalations": result.get("escalations", [])}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
