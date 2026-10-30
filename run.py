"""SilverFile command-line entry point.

    python run.py --help
    python run.py tax-math-server    start the tax_math MCP server on stdin/stdout
    python run.py extract IMAGE...   read W-2 images with the Document Extraction agent
    python run.py eligibility FILE   run Eligibility & Rules on a state file (JSON)
    python run.py intake             run (or resume) an intake interview in the terminal
    python run.py review             review a paused session as a preparer
    python run.py session            run (or resume) a whole session in the terminal: W-2 upload,
                                     typed-in forms, interview, every agent, draft Form 1040-SR
    python run.py dashboard          the Streamlit dashboard, for taxpayers and Kushim preparers

Still to add: the batch run with --no-context-strategy.
"""

import argparse
import sys
from pathlib import Path

# src/ is the import root, so code imports tax_math, state, agents.intake and so on.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["session"]:  # its options start with "--", which REMAINDER won't take
        from app.cli import main as run_session

        run_session(argv[1:])
        return
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="SilverFile: a draft Form 1040-SR preparation aid (tax year 2025).",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    commands.add_parser(
        "tax-math-server",
        help="start the tax_math MCP server on stdin/stdout (used by the Computation agent)",
    )
    extract = commands.add_parser(
        "extract",
        help="read W-2 images with the Document Extraction agent (add --offline to skip the model)",
        add_help=False,
    )
    extract.add_argument("extract_args", nargs=argparse.REMAINDER)
    eligibility = commands.add_parser(
        "eligibility",
        help="run the Eligibility & Rules agent on a state file (JSON); no API key needed",
        add_help=False,
    )
    eligibility.add_argument("eligibility_args", nargs=argparse.REMAINDER)
    intake = commands.add_parser(
        "intake", help="run or resume a taxpayer intake interview in the terminal"
    )
    intake.add_argument("--session", default="demo_taxpayer_001", help="session ID to start or resume")
    intake.add_argument("--db", default=None, help="SQLite file for saved sessions")
    review = commands.add_parser(
        "review", help="review a session that is waiting for a preparer's decision"
    )
    review.add_argument("--session", required=True, help="session ID to review")
    review.add_argument("--db", default=None, help="SQLite file for saved sessions")
    commands.add_parser(
        "session", help="run or resume a whole session in the terminal (see run.py session --help)"
    )
    dashboard = commands.add_parser(
        "dashboard", help="start the Streamlit dashboard (taxpayer and preparer views)"
    )
    dashboard.add_argument("--db", default=None, help="SQLite file for saved sessions")
    dashboard.add_argument("--offline", action="store_true",
                           help="read W-2s with the offline test reader instead of the vision model")
    dashboard.add_argument("--port", default="8501")
    args = parser.parse_args(argv)

    if args.command == "tax-math-server":
        from tax_math.server import main as serve

        serve()
    elif args.command == "extract":
        from agents.extraction.cli import main as extract_main

        extract_main(args.extract_args)
    elif args.command == "eligibility":
        from agents.eligibility.cli import main as eligibility_main

        eligibility_main(args.eligibility_args)
    elif args.command == "intake":
        from agents.intake import main as run_intake

        run_intake(["--session", args.session] + (["--db", args.db] if args.db else []))
    elif args.command == "review":
        from agents.orchestrator.human_review import main as run_review

        run_review(["--session", args.session] + (["--db", args.db] if args.db else []))
    elif args.command == "dashboard":
        import subprocess

        app = Path(__file__).resolve().parent / "src" / "app" / "dashboard.py"
        extra = (["--db", args.db] if args.db else []) + (["--offline"] if args.offline else [])
        # Headless: no first-run e-mail prompt and no usage statistics; open the printed URL.
        print(f"SilverFile dashboard: open http://localhost:{args.port} in a browser (Ctrl+C to stop).")
        subprocess.run([sys.executable, "-m", "streamlit", "run", str(app), "--server.port", args.port,
                        "--server.headless", "true", "--browser.gatherUsageStats", "false",
                        "--", *extra], check=False)


if __name__ == "__main__":
    main()
