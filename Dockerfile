# SilverFile in one container: the dashboard, the terminal session runner and the tax_math MCP
# server (which Computation and Review start as a subprocess). See "Run with Docker" in README.md.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Saved sessions live on the /state volume, so they survive a container restart.
    SILVERFILE_DB=/state/sessions.sqlite

WORKDIR /app

# Dependencies first, so editing the code doesn't reinstall them.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN mkdir -p /state /app/out /app/data/uploads && chmod +x docker-entrypoint.sh

EXPOSE 8501
ENTRYPOINT ["./docker-entrypoint.sh"]
# No arguments: start the dashboard. Any arguments are passed to run.py, for example
# `docker compose run --rm silverfile session --session eval1 ...`.
CMD []
