#!/bin/sh
# Container entry point.
#
# No arguments: start the dashboard on port 8501. With an API key in the environment (from .env),
# W-2 images are read by the vision model; without one, the offline reader is used, which only
# knows the demo W-2s (it reads the ground-truth JSON stored next to each image).
#
# With arguments: run them as a run.py command, e.g. `session ...`, `review ...`, or `pytest`.
set -e

if [ "$#" -eq 0 ]; then
    if [ -n "$ANTHROPIC_API_KEY" ] || [ -n "$OPENAI_API_KEY" ]; then
        echo "API key found: W-2 images will be read by the vision model."
        exec python run.py dashboard
    fi
    echo "No API key in .env: using the offline W-2 reader (demo W-2s only)."
    exec python run.py dashboard --offline
fi

if [ "$1" = "pytest" ]; then
    shift
    exec python -m pytest "$@"
fi

exec python run.py "$@"
