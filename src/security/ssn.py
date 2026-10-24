"""SSN handling for SilverFile (Security role).

SilverFile is a Cornell teaching system, so it may ONLY accept the SSA's
reserved test range: SSNs whose first three digits are 900-999. Anything else
is treated as a possible *real* SSN and is never stored, logged, or echoed.

Use these helpers everywhere an SSN enters the system: Intake (taxpayer,
spouse, dependents) and Document Extraction (W-2 box a).
"""

from __future__ import annotations

import re
from typing import Literal

SSNStatus = Literal["valid_test", "not_test_range", "malformed", "missing"]

# 9 digits, optionally grouped 3-2-4 with a consistent separator ("-" or space).
# Look-arounds stop us matching inside longer digit runs (account numbers etc.).
_SSN_PATTERN = re.compile(r"(?<!\d)(\d{3})([-\s]?)(\d{2})\2(\d{4})(?!\d)")

REDACTED = "[SSN REDACTED]"


def normalize_ssn(raw: str | None) -> str | None:
    """Return the 9 digits of an SSN, or None if it isn't SSN-shaped."""
    if raw is None:
        return None
    match = _SSN_PATTERN.fullmatch(raw.strip())
    if not match:
        return None
    return match.group(1) + match.group(3) + match.group(4)


def check_ssn(raw: str | None) -> SSNStatus:
    """Classify an SSN without ever returning the number itself."""
    if raw is None or not raw.strip():
        return "missing"
    digits = normalize_ssn(raw)
    if digits is None:
        return "malformed"
    if 900 <= int(digits[:3]) <= 999:
        return "valid_test"
    return "not_test_range"


def is_test_ssn(raw: str | None) -> bool:
    return check_ssn(raw) == "valid_test"


def mask_ssn(raw: str | None) -> str:
    """Safe display form. Only test-range SSNs keep their last four digits;
    anything that might be real is fully redacted."""
    if check_ssn(raw) != "valid_test":
        return REDACTED
    digits = normalize_ssn(raw)
    return f"XXX-XX-{digits[-4:]}"


def redact_ssns(text: str) -> str:
    """Scrub every SSN-shaped number from free text (user messages, model
    output, error strings) before it reaches state, traces or logs."""

    def _replace(match: re.Match) -> str:
        digits = match.group(1) + match.group(3) + match.group(4)
        if 900 <= int(digits[:3]) <= 999:
            return f"XXX-XX-{digits[-4:]}"
        return REDACTED

    return _SSN_PATTERN.sub(_replace, text)


def contains_ssn(text: str) -> bool:
    """True if free text contains anything SSN-shaped (e.g. volunteered by a user)."""
    return bool(_SSN_PATTERN.search(text))
