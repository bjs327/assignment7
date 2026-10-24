"""SilverFile command-line entry point.

    python run.py --help
    python run.py tax-math-server    start the tax_math MCP server on stdin/stdout
    python run.py intake             run (or resume) an intake interview in the terminal

Commands for the rest of the system (the intake session, the dashboard, the batch run
with --no-context-strategy) get added here as the agents land.
"""

import argparse
import sys
from pathlib import Path

# src/ is the import root, so code imports tax_math, state, agents.intake and so on.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="SilverFile: a draft Form 1040-SR preparation aid (tax year 2025).",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    commands.add_parser(
        "tax-math-server",
        help="start the tax_math MCP server on stdin/stdout (used by the Computation agent)",
    )
    intake = commands.add_parser(
        "intake", help="run or resume a taxpayer intake interview in the terminal"
    )
    intake.add_argument("--session", default="demo_taxpayer_001", help="session ID to start or resume")
    intake.add_argument("--db", default=None, help="SQLite file for saved sessions")
    args = parser.parse_args(argv)

    if args.command == "tax-math-server":
        from tax_math.server import main as serve

        serve()
    elif args.command == "intake":
        from agents.intake import main as run_intake

        run_intake(["--session", args.session] + (["--db", args.db] if args.db else []))


if __name__ == "__main__":
    main()
