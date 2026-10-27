"""Tests of document registration (src/app/documents.py): the upload interface between the
taxpayer and Document Extraction, and typed-in forms. No API key needed."""

import pytest

from agents.eligibility.facts import facts_from_state
from app.documents import (
    RegistrationError,
    enter_amount,
    enter_document,
    load_forms_file,
    register_path,
    register_upload,
)

from ..orchestrator.scenarios import ROOT

W2 = ROOT / "demo" / "w2_image_1.jpg"


def test_an_uploaded_w2_gets_an_id_a_safe_path_and_a_hash(tmp_path):
    doc = register_upload("s1", "Margaret Testwell W2.JPG", W2.read_bytes(), [], upload_root=tmp_path)
    assert doc["document_id"] == "w2-1" and doc["document_type"] == "W-2"
    assert doc["path"] == str(tmp_path / "s1" / "w2-1.jpg")  # the taxpayer's file name isn't kept
    assert len(doc["sha256"]) == 64 and doc["registered_at"]


def test_the_same_file_twice_is_refused_and_ids_count_up(tmp_path):
    first = register_upload("s1", "a.jpg", W2.read_bytes(), [], upload_root=tmp_path)
    with pytest.raises(RegistrationError, match="already been uploaded as w2-1"):
        register_upload("s1", "b.jpg", W2.read_bytes(), [first], upload_root=tmp_path)
    second = register_path("s1", ROOT / "demo" / "w2_image_2.jpg", [first], upload_root=tmp_path)
    assert second["document_id"] == "w2-2"


@pytest.mark.parametrize("name,data,message", [
    ("w2.pdf", b"%PDF-1.4", "JPG, PNG or WEBP"),
    ("w2.jpg", b"", "empty"),
    ("w2.jpg", b"not an image at all", "doesn't open as an image"),
])
def test_unusable_uploads_are_refused(tmp_path, name, data, message):
    with pytest.raises(RegistrationError, match=message):
        register_upload("s1", name, data, [], upload_root=tmp_path)


def test_a_session_id_containing_an_ssn_is_refused(tmp_path):
    with pytest.raises(ValueError):
        register_upload("900-12-3456", "w2.jpg", W2.read_bytes(), [], upload_root=tmp_path)


def test_the_offline_readers_ground_truth_is_copied_with_the_image(tmp_path):
    doc = register_path("s1", W2, [], upload_root=tmp_path)
    assert (tmp_path / "s1" / "w2-1.json").is_file()
    assert doc["document_id"] == "w2-1"


def test_a_typed_in_form_records_who_entered_it_and_when():
    doc = enter_document("1099-INT", "taxpayer", {"1": "$1,200.00", "8": "300", "3": ""}, "taxpayer", [])
    assert doc["document_id"] == "int-1"
    assert doc["boxes"] == {"1": "1200.00", "8": "300"}  # as typed, commas and $ removed, never rounded
    assert doc["entered_by"] == "taxpayer" and doc["entered_at"]
    assert doc["tax_year"] == 2025 and doc["source"] == "entered"


def test_a_1099r_keeps_both_flags_and_normalises_the_code():
    doc = enter_document("1099-R", "spouse", {"1": "100", "2a": "100", "7": " 7y ", "ira_sep_simple": True},
                         "preparer Ann", [])
    assert doc["boxes"]["7"] == "7Y"
    assert doc["boxes"]["ira_sep_simple"] is True and doc["boxes"]["2b_taxable_not_determined"] is False


@pytest.mark.parametrize("doc_type,boxes,message", [
    ("1099-NEC", {"1": "10"}, "Forms that can be typed in"),
    ("SSA-1099", {}, "Box 5"),
    ("SSA-1099", {"5": "-10"}, "isn't an amount"),
    ("SSA-1099", {"5": "12.345"}, "isn't an amount"),
    ("SSA-1099", {"5": "10", "9": "1"}, "no box 9"),
    ("1099-R", {"1": "10", "2a": "10", "7": "seven"}, "isn't a distribution code"),
])
def test_bad_typed_in_forms_are_refused(doc_type, boxes, message):
    with pytest.raises(RegistrationError, match=message):
        enter_document(doc_type, "taxpayer", boxes, "taxpayer", [])


def test_a_typed_in_form_needs_to_say_who_entered_it():
    with pytest.raises(RegistrationError, match="who is entering"):
        enter_document("SSA-1099", "taxpayer", {"5": "10"}, "  ", [])


def test_direct_line_entries_use_computations_format():
    line, entry = enter_amount("10", "500", "taxpayer")
    assert line == "10" and entry["amount"] == "500" and entry["entered_by"] == "taxpayer" and entry["entered_at"]
    with pytest.raises(RegistrationError, match="Lines that can be entered"):
        enter_amount("1z", "500", "taxpayer")  # wages come from the W-2, not typed in


def test_typed_in_forms_are_read_by_eligibility_and_rules():
    documents, amounts = load_forms_file({"documents": [
        {"document_type": "SSA-1099", "boxes": {"5": "24000.00"}},
        {"document_type": "1099-R", "boxes": {"1": "40000", "2a": "40000", "7": "7", "ira_sep_simple": True}},
    ], "amounts": {"10": "250"}})
    facts = facts_from_state({"entered_documents": documents})
    assert [(d.document_id, d.document_type, d.boxes.get("5")) for d in facts.documents] == [
        ("r-1", "1099-R", None), ("ssa-1", "SSA-1099", "24000.00")]
    assert amounts["10"]["amount"] == "250"
