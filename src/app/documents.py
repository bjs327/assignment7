"""Document registration: the upload interface between the taxpayer and Document Extraction.

Not an agent. It records what the taxpayer supplies and puts it where the agents read it:

- **W-2 images** (``register_upload``): checked, given a unique ID, saved under
  ``data/uploads/<session>/`` with a file name SilverFile chooses (the taxpayer's own file name
  can contain their name, so it isn't kept), hashed, and listed in ``documents``, which Document
  Extraction reads. The same file uploaded twice is refused.
- **Other forms typed in** (``enter_document``): SSA-1099, 1099-INT, 1099-DIV and 1099-R boxes,
  in the ``entered_documents`` format Eligibility & Rules and Computation already read.
- **Schedule 1 to 3 lines typed in** (``enter_amount``): ``entered_amounts``, keyed by form line.

Typed values are "direct user input" (specification section 4.2): each record says who entered
it and when. Amounts are checked to be well-formed money and stored as the person typed them
(commas and "$" removed). Nothing here adds, rounds or interprets an amount; Computation does
the arithmetic through tax_math and Eligibility & Rules decides what a form means.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from agents.computation.inputs import ENTERED_LINES
from memory import session_config

REPO_ROOT = Path(__file__).resolve().parents[2]
UPLOAD_ROOT = REPO_ROOT / "data" / "uploads"  # kept out of git by .gitignore

# The image types Document Extraction reads (extraction/extractor.py, load_image).
ACCEPTED_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
MAX_UPLOAD_BYTES = 15_000_000  # Extraction shrinks anything over 3.5 MB before sending it
TAX_YEAR = 2025
RECIPIENTS = ("taxpayer", "spouse")

# Forms typed in by hand, the boxes SilverFile uses from each (computation/inputs.py BOX_LINES
# and the 1099-R facts Eligibility & Rules reads), and how each box is asked for.
ENTERED_FORMS: dict[str, dict[str, tuple[str, str]]] = {
    "SSA-1099": {
        "5": ("money", "Box 5, net benefits for 2025"),
        "6": ("money", "Box 6, voluntary federal income tax withheld"),
    },
    "1099-INT": {
        "1": ("money", "Box 1, interest income"),
        "3": ("money", "Box 3, interest on U.S. savings bonds and Treasury obligations"),
        "4": ("money", "Box 4, federal income tax withheld"),
        "8": ("money", "Box 8, tax-exempt interest"),
    },
    "1099-DIV": {
        "1a": ("money", "Box 1a, total ordinary dividends"),
        "1b": ("money", "Box 1b, qualified dividends"),
        "2a": ("money", "Box 2a, total capital gain distributions"),
        "4": ("money", "Box 4, federal income tax withheld"),
    },
    "1099-R": {
        "1": ("money", "Box 1, gross distribution"),
        "2a": ("money", "Box 2a, taxable amount"),
        "2b_taxable_not_determined": ("flag", "Box 2b, 'taxable amount not determined' is checked"),
        "4": ("money", "Box 4, federal income tax withheld"),
        "7": ("code", "Box 7, distribution code(s), for example 7 or 7Y"),
        "ira_sep_simple": ("flag", "The IRA/SEP/SIMPLE box (next to box 7) is checked"),
        "qcd_amount": ("money", "Code Y only: how much went directly to charity"),
    },
}
ID_PREFIX = {"W-2": "w2", "SSA-1099": "ssa", "1099-INT": "int", "1099-DIV": "div", "1099-R": "r"}

_MONEY = re.compile(r"^\d+(\.\d{1,2})?$")
_CODE = re.compile(r"^[0-9A-Z]{1,2}$")


class RegistrationError(ValueError):
    """The upload or entry was refused. The message is safe to show the person."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _next_id(document_type: str, existing: list[dict[str, Any]]) -> str:
    prefix = ID_PREFIX[document_type]
    taken = {d.get("document_id") for d in existing}
    n = 1
    while f"{prefix}-{n}" in taken:
        n += 1
    return f"{prefix}-{n}"


def parse_money(text: Any) -> str:
    """A non-negative amount as typed, with "$" and thousands commas removed. Not rounded."""
    cleaned = str(text if text is not None else "").strip().replace("$", "").replace(",", "")
    if not _MONEY.match(cleaned):
        raise RegistrationError(f"'{text}' isn't an amount. Please type dollars and cents, like 1234.56.")
    try:
        Decimal(cleaned)
    except InvalidOperation as err:  # pragma: no cover - the pattern already rules this out
        raise RegistrationError(f"'{text}' isn't an amount.") from err
    return cleaned


# --- W-2 images ------------------------------------------------------------------------------


def register_upload(session_id: str, filename: str, data: bytes, existing: list[dict[str, Any]],
                    *, upload_root: Path | None = None, sidecar: bytes | None = None) -> dict[str, Any]:
    """Save one uploaded W-2 image and return its ``documents`` entry.

    ``existing``: the documents already registered for this session, used for the next ID and
    to refuse a duplicate. ``sidecar``: offline demo only, the ground-truth JSON the offline
    W-2 reader (FixtureW2Extractor) reads next to the image.
    """
    session_config(session_id)  # rejects unsafe IDs, and any that contain an SSN
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ACCEPTED_SUFFIXES:
        raise RegistrationError("Please upload a photo or scan of the W-2 as a JPG, PNG or WEBP image.")
    if not data:
        raise RegistrationError("That file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise RegistrationError("That file is too large (the limit is 15 MB).")
    _check_image(data)
    digest = hashlib.sha256(data).hexdigest()
    for doc in existing:
        if doc.get("sha256") == digest:
            raise RegistrationError(f"This file has already been uploaded as {doc['document_id']}.")

    document_id = _next_id("W-2", existing)
    folder = (upload_root or UPLOAD_ROOT) / session_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{document_id}{suffix}"
    path.write_bytes(data)
    if sidecar is not None:
        path.with_suffix(".json").write_bytes(sidecar)
    return {"document_id": document_id, "document_type": "W-2", "path": str(path),
            "sha256": digest, "registered_at": _now()}


def register_path(session_id: str, source: str | Path, existing: list[dict[str, Any]],
                  *, upload_root: Path | None = None) -> dict[str, Any]:
    """``register_upload`` for a file on disk (the terminal program). A ground-truth JSON next
    to the image, as the demo W-2s have, is copied too, for the offline reader."""
    source = Path(source)
    if not source.is_file():
        raise RegistrationError(f"No file at {source}.")
    sidecar = source.with_suffix(".json")
    return register_upload(session_id, source.name, source.read_bytes(), existing, upload_root=upload_root,
                           sidecar=sidecar.read_bytes() if sidecar.is_file() else None)


def _check_image(data: bytes) -> None:
    from io import BytesIO

    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(BytesIO(data)) as img:
            img.verify()
    except (UnidentifiedImageError, OSError) as err:
        raise RegistrationError("That file doesn't open as an image.") from err


# --- Forms typed in --------------------------------------------------------------------------


def enter_document(document_type: str, recipient: str, boxes: dict[str, Any], entered_by: str,
                   existing: list[dict[str, Any]]) -> dict[str, Any]:
    """Check one typed-in form and return its ``entered_documents`` entry. Blank boxes are left
    out; flags are kept only when checked."""
    if document_type not in ENTERED_FORMS:
        raise RegistrationError("Forms that can be typed in: " + ", ".join(ENTERED_FORMS) + ".")
    if recipient not in RECIPIENTS:
        raise RegistrationError("Say whose form this is: the taxpayer's or the spouse's.")
    who = str(entered_by or "").strip()
    if not who:
        raise RegistrationError("Please say who is entering this form.")
    spec = ENTERED_FORMS[document_type]
    unknown = [b for b in boxes if b not in spec]
    if unknown:
        raise RegistrationError(f"{document_type} has no box {', '.join(unknown)} that SilverFile uses.")

    clean: dict[str, Any] = {}
    for box, (kind, label) in spec.items():
        value = boxes.get(box)
        if kind == "flag":
            if value:
                clean[box] = True
            elif document_type == "1099-R":
                clean[box] = False  # Eligibility & Rules reads both 1099-R flags either way
            continue
        if value is None or str(value).strip() == "":
            continue
        if kind == "money":
            clean[box] = parse_money(value)
        else:
            code = str(value).strip().upper().replace(" ", "")
            if not _CODE.match(code):
                raise RegistrationError(f"{label}: '{value}' isn't a distribution code.")
            clean[box] = code
    required = {"SSA-1099": ["5"], "1099-R": ["1", "2a", "7"]}.get(document_type, [])
    missing = [spec[b][1] for b in required if b not in clean]
    if missing:
        raise RegistrationError("Please fill in: " + "; ".join(missing) + ".")
    if document_type == "1099-INT" and not any(b in clean for b in ("1", "3", "8")):
        raise RegistrationError("Please fill in at least one interest box (1, 3 or 8).")
    if document_type == "1099-DIV" and "1a" not in clean:
        raise RegistrationError("Please fill in box 1a, total ordinary dividends.")

    return {"document_id": _next_id(document_type, existing), "document_type": document_type,
            "recipient": recipient, "tax_year": TAX_YEAR, "source": "entered", "boxes": clean,
            "entered_by": who, "entered_at": _now()}


def enter_amount(line: str, amount: Any, entered_by: str) -> tuple[str, dict[str, Any]]:
    """Check one directly entered Schedule 1 to 3 line and return ``(line, entered_amounts entry)``."""
    if line not in ENTERED_LINES:
        raise RegistrationError("Lines that can be entered directly: " + ", ".join(ENTERED_LINES) + ".")
    who = str(entered_by or "").strip()
    if not who:
        raise RegistrationError("Please say who is entering this amount.")
    return line, {"amount": parse_money(amount), "entered_by": who, "entered_at": _now()}


def load_forms_file(data: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Read a forms file for the terminal program and the demo cases::

        {"entered_by": "taxpayer",
         "documents": [{"document_type": "SSA-1099", "recipient": "taxpayer", "boxes": {"5": "24000.00"}}],
         "amounts": {"10": "500.00"}}

    Every entry goes through ``enter_document`` / ``enter_amount``, so it is checked the same way
    as one typed into the dashboard.
    """
    who = data.get("entered_by") or "taxpayer"
    documents: list[dict[str, Any]] = []
    for d in data.get("documents") or []:
        documents.append(enter_document(d.get("document_type"), d.get("recipient", "taxpayer"),
                                        d.get("boxes") or {}, d.get("entered_by") or who, documents))
    amounts = dict(enter_amount(line, value, who) for line, value in (data.get("amounts") or {}).items())
    return documents, amounts
