"""Terminal program for a whole SilverFile session:

    python run.py session --session demo_1 --w2 demo/w2_image_1.jpg --forms demo/eval_case_1_forms.json

Registers the W-2 images and typed-in forms, runs the interview one question at a time, and
lets every agent run. If the run pauses for a preparer, it says so and stops; a preparer
decides in the dashboard (or ``python run.py review``), then running this again with the same
``--session`` carries on. When the run finishes, the draft Form 1040-SR is written as HTML.

Run it again with the same ``--session`` to resume: documents and forms are only registered when
the session starts, never twice.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app import session as sessions
from app.documents import RegistrationError, load_forms_file, register_path
from app.form1040sr import build_form, render_html

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "out"  # kept out of git


def write_form(package: dict, out: Path | None = None) -> Path:
    form = build_form(package)
    path = out or OUT_DIR / f"{form['session_id']}_1040sr.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(form), encoding="utf-8")
    return path


def _report_finished(s: dict, out: Path | None) -> None:
    path = write_form(s["package"], out)
    if s["run_status"] == "approved":
        print("\nSilverFile: Thank you. Your draft return is ready for a Kushim preparer to check.")
    else:
        print("\nSilverFile: A Kushim preparer is taking over your return from here.")
    print(f"Run status: {s['run_status']}. Draft Form 1040-SR written to {path}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="run.py session", description=__doc__.split("\n\n")[0])
    parser.add_argument("--session", required=True, help="session ID to start or resume")
    parser.add_argument("--w2", action="append", default=[], metavar="IMAGE", help="a W-2 image (repeat for each)")
    parser.add_argument("--forms", metavar="JSON", help="typed-in forms and amounts (see demo/eval_case_1_forms.json)")
    parser.add_argument("--offline", action="store_true",
                        help="read W-2s with the offline test reader (needs the .json next to each image)")
    parser.add_argument("--db", default=None, help="SQLite file for saved sessions (default: data/sessions.sqlite)")
    parser.add_argument("--out", default=None, help="where to write the draft form (default: out/<session>_1040sr.html)")
    parser.add_argument("--answers", metavar="FILE",
                        help="answer questions from a text file, one answer per line, instead of the keyboard")
    args = parser.parse_args(argv)
    out = Path(args.out) if args.out else None

    graph = sessions.open_graph(args.db, offline=args.offline)
    s = sessions.status(graph, args.session)
    if s["state"] == "not_started":
        try:
            documents: list[dict] = []
            for path in args.w2:
                documents.append(register_path(args.session, path, documents))
            entered, amounts = ([], {})
            if args.forms:
                entered, amounts = load_forms_file(json.loads(Path(args.forms).read_text()))
        except (RegistrationError, json.JSONDecodeError) as err:
            raise SystemExit(f"Not started: {err}") from None
        print(f"Registered {len(documents)} W-2 image(s) and {len(entered)} typed-in form(s).")
        s = sessions.start(graph, args.session, documents=documents, entered_documents=entered,
                           entered_amounts=amounts)
    else:
        if args.w2 or args.forms:
            print("This session already exists, so --w2 and --forms are ignored. Resuming.")
        if s["state"] == "question":
            print("\nSilverFile: " + sessions.welcome_back(s))

    scripted = Path(args.answers).read_text().splitlines() if args.answers else None
    while s["state"] == "question":
        q = s["question"]
        if q.get("notice"):
            print(f"\nSilverFile: {q['notice']}")
        print(f"\nSilverFile ({q['progress']['answered']} of {q['progress']['total']} answered): {q['question']}")
        if q.get("current_answer"):
            print(f"  (Your answer so far: {q['current_answer']})")
        if scripted is not None:
            if not scripted:
                print("\nNo more scripted answers. Your answers are saved; run this again to continue.")
                return
            text = scripted.pop(0)
            print(f"> {text}")
        else:
            try:
                text = input("> ")
            except (EOFError, KeyboardInterrupt):
                print("\nYour answers are saved. Run the same command again to continue.")
                return
        s = sessions.answer(graph, args.session, q["question_id"], text)

    if s["state"] == "preparer":
        if s.get("message"):
            print(f"\nSilverFile: {s['message']}")
        issues = s["packet"]["issues"]
        print(f"\nThis session is waiting for a Kushim preparer ({len(issues)} issue(s)):")
        for issue in issues:
            print(f"  - {issue['title']}: {issue['reason']}")
        print("\nA preparer can decide in the dashboard (python run.py dashboard) or with:\n"
              f"  python run.py review --session {args.session}"
              + (f" --db {args.db}" if args.db else "")
              + f"\nThen run this command again with --session {args.session} to continue.")
    elif s["state"] == "finished":
        _report_finished(s, out)
    else:
        print(f"Session {args.session} is in an unexpected state: {s['state']}.")
