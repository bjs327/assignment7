"""Writes one saved session's run trace as JSON: the route the Orchestrator took, the task ledger,
each agent's trace events, escalations, human decisions and Review's verdicts.

    python traces/export_trace.py --session eval2 --out traces/eval_case_2.json

Reads the checkpoint only; nothing is run. Social Security numbers are redacted.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app import session as sessions  # noqa: E402
from memory import DEFAULT_DB_PATH, load_session, redact_ssns  # noqa: E402


def export(graph, session_id: str) -> dict:
    snap = load_session(graph, session_id)
    if not snap.exists:
        raise SystemExit(f"No saved session {session_id!r}.")
    v = snap.values
    return redact_ssns({
        "session_id": session_id,
        "run_status": v.get("run_status"),
        "waiting_for": [p.get("type") for p in snap.pending if isinstance(p, dict)],
        "route_history": v.get("route_history") or [],
        "task_ledger": v.get("task_ledger") or {},
        "trace": v.get("trace") or [],
        "escalations": v.get("escalations") or [],
        "intake_escalations": v.get("intake_escalations") or [],
        "human_decisions": v.get("human_decisions") or [],
        "review_history": [{k: h.get(k) for k in ("iteration", "verdict", "summary")}
                           for h in v.get("review_history") or []],
    })


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--session", required=True)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--out", type=Path, help="default: traces/<session>.json")
    args = parser.parse_args(argv)
    out = args.out or ROOT / "traces" / f"{args.session}.json"
    record = export(sessions.open_graph(args.db, offline=True), args.session)
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(f"Wrote {out}: {len(record['route_history'])} hand-offs, {len(record['trace'])} trace events.")


if __name__ == "__main__":
    main()
