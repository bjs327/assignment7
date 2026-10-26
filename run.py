"""SilverFile command-line entry point.

    python run.py --help
    python run.py tax-math-server    start the tax_math MCP server on stdin/stdout
    python run.py extract IMAGE...   read W-2 images with the Document Extraction agent
    python run.py eligibility FILE   run Eligibility & Rules on a state file (JSON)

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


if __name__ == "__main__":
    main()
