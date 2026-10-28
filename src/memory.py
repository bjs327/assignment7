"""Agent memory: the checkpointer that lets a session abandoned on Tuesday resume on Friday,
and the context-window strategy measured in docs/metrics.md (specification sections 4.3 and 4.5).

Two kinds of memory:

- **Durable session memory.** Every LangGraph super-step is checkpointed under the session's
  ``thread_id``. With the SQLite checkpointer the checkpoints live in a file, so a taxpayer can
  close the window mid-interview and pick up at the same question days later, even after the
  process restarts. ``load_session`` reads a session back without running anything.
- **Working (context-window) memory.** ``build_context`` compacts a session into what an agent
  actually needs on its next turn: a summary of the facts collected so far, any open
  escalations, and only the last few turns of conversation. The full transcript stays in the
  checkpoint; it is never replayed whole. ``estimate_tokens`` gives the rough size so the batch
  run in docs/metrics.md can compare the strategy on and off.

Nothing here knows about tax rules. Everything that leaves this module through a summary or a
transcript has Social Security numbers redacted.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

# Default location for durable sessions. Kept out of git by the *.sqlite rule in .gitignore.
# SILVERFILE_DB overrides it (the Docker image sets it so sessions live on a volume).
DEFAULT_DB_PATH = Path(
    os.environ.get("SILVERFILE_DB")
    or Path(__file__).resolve().parent.parent / "data" / "sessions.sqlite"
)

# Any nine digits written like an SSN: 123-45-6789, 123 45 6789 or 123456789.
# Not part of a longer number, an amount or a decimal, but sentence punctuation is fine.
SSN_LIKE = re.compile(r"(?<!\d)(?<!\$)(?<!\d[.,])(\d{3})[- ]?(\d{2})[- ]?(\d{4})(?!\d|[.,]\d)")
SSN_PLACEHOLDER = "[SSN redacted]"

_SESSION_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


# --- Checkpointing -------------------------------------------------------------------------


def make_checkpointer(db_path: str | Path | None = None) -> BaseCheckpointSaver:
    """Return a checkpointer.

    With no path, an in-memory saver (tests, demos: lost when the process exits). With a path,
    a SQLite saver whose sessions survive restarts. Use ``":memory:"`` for a throwaway SQLite
    database, which exercises the same code path as the file-backed one.
    """
    if db_path is None:
        return InMemorySaver()

    from langgraph.checkpoint.sqlite import SqliteSaver

    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


def session_config(session_id: str) -> dict[str, Any]:
    """The LangGraph config that addresses one taxpayer session.

    Session IDs are opaque handles chosen by the caller, never personal data: they are
    rejected if they contain anything that looks like an SSN.
    """
    if not isinstance(session_id, str) or not _SESSION_ID.match(session_id):
        raise ValueError(
            "session_id must be 1-128 characters of letters, digits, '_', '-', '.' or ':'"
        )
    if SSN_LIKE.search(session_id):
        raise ValueError("session_id must not contain a Social Security number")
    return {"configurable": {"thread_id": session_id}}


def list_sessions(checkpointer: BaseCheckpointSaver) -> list[str]:
    """Every session ID the checkpointer holds, sorted."""
    return sorted({c.config["configurable"]["thread_id"] for c in checkpointer.list(None)})


@dataclass
class SessionSnapshot:
    """A read-only view of a session, taken without running the graph."""

    session_id: str
    exists: bool
    values: dict[str, Any] = field(default_factory=dict)
    pending: list[Any] = field(default_factory=list)  # payloads of any interrupt() waiting
    next_nodes: tuple[str, ...] = ()

    @property
    def is_waiting_for_input(self) -> bool:
        return bool(self.pending)

    @property
    def is_finished(self) -> bool:
        return self.exists and not self.next_nodes and not self.pending


def load_session(graph: Any, session_id: str) -> SessionSnapshot:
    """Read a session's latest checkpoint from a compiled graph that has a checkpointer."""
    snapshot = graph.get_state(session_config(session_id))
    exists = snapshot.created_at is not None
    pending = [i.value for task in snapshot.tasks for i in task.interrupts]
    return SessionSnapshot(
        session_id=session_id,
        exists=exists,
        values=dict(snapshot.values or {}),
        pending=pending,
        next_nodes=tuple(snapshot.next or ()),
    )


# --- Redaction -----------------------------------------------------------------------------


def contains_ssn(text: str) -> bool:
    return bool(SSN_LIKE.search(text or ""))


def redact_ssns(value: Any) -> Any:
    """Replace anything that looks like an SSN, in a string or nested list/dict."""
    if isinstance(value, str):
        return SSN_LIKE.sub(SSN_PLACEHOLDER, value)
    if isinstance(value, dict):
        return {k: redact_ssns(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_ssns(v) for v in value]
    return value


# --- Context-window strategy ---------------------------------------------------------------

DEFAULT_RECENT_TURNS = 6


def estimate_tokens(value: Any) -> int:
    """Rough token count (about four characters per token) for comparing context sizes."""
    text = value if isinstance(value, str) else json.dumps(value, default=str, sort_keys=True)
    return (len(text) + 3) // 4


def build_context(
    facts: dict[str, Any],
    transcript: list[dict[str, Any]],
    escalations: list[dict[str, Any]] | None = None,
    *,
    max_recent_turns: int | None = DEFAULT_RECENT_TURNS,
) -> dict[str, Any]:
    """Compact a session into the working context for the next turn.

    ``facts`` is the structured memory (answers collected so far), which replaces the older
    conversation. Only the last ``max_recent_turns`` transcript entries are kept verbatim.
    Pass ``max_recent_turns=None`` to disable the strategy and keep the whole transcript, which
    is the baseline for the ``--no-context-strategy`` run.
    """
    recent = list(transcript) if max_recent_turns is None else list(transcript)[-max_recent_turns:]
    if max_recent_turns == 0:
        recent = []
    return redact_ssns(
        {
            "facts": facts,
            "open_escalations": [
                {"type": e.get("type"), "reason": e.get("reason")} for e in (escalations or [])
            ],
            "omitted_turns": len(transcript) - len(recent),
            "recent_turns": recent,
        }
    )
