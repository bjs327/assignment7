import json
from decimal import Decimal

from agents.extraction.schemas import W2Reading
from agents.extraction.validation import build_w2_result, parse_amount

GOOD = {
    "tax_year": "2025", "employee_ssn": "900-12-3456", "employer_ein": "12-3456789",
    "employer_name": "Test Employer", "employee_name": "Pat Test",
    "box1_wages": "$18,500.00", "box2_federal_withheld": "1,850.00",
    "box3_ss_wages": "18500", "box4_ss_withheld": "1,147.00",
    "box5_medicare_wages": "18,500.00", "box6_medicare_withheld": "268.25",
}


def _build(**overrides):
    reading = W2Reading.model_validate({**GOOD, **overrides})
    return build_w2_result(document={"document_id": "w2-1"}, reading=reading, file_name="w2.jpg",
                           file_sha256="abc", method="test", extracted_at="2026-01-01T00:00:00+00:00",
                           attempt=1)


def _codes(result):
    return {i.code for i in result.issues}


def test_parse_amount():
    assert parse_amount("$18,500.00") == (Decimal("18500.00"), None)
    assert parse_amount("18 500") == (Decimal("18500.00"), None)
    assert parse_amount("268.2") == (Decimal("268.20"), None)
    assert parse_amount("") == (None, "blank")
    assert parse_amount("-50.00")[1] == "negative"
    assert parse_amount("(50.00)")[1] == "negative"
    assert parse_amount("1,8S0.00")[1] == "not_a_number"
    assert parse_amount("12.345")[1] == "not_a_number"


def test_clean_w2_has_provenance_on_every_amount():
    r = _build()
    assert r.status == "extracted" and not _codes(r)
    assert r.fields["box1"].value == "18500.00"
    assert r.fields["box2"].value == "1850.00"
    for f in r.fields.values():
        p = f.provenance
        assert p.document_id == "w2-1" and p.location == f"W-2 box {f.box}" and p.raw_text
    assert r.identifiers.employee_ssn_masked == "XXX-XX-3456"


def test_real_looking_ssn_is_escalated_and_never_stored():
    r = _build(employee_ssn="123-45-6789")
    assert r.status == "needs_review" and "non_test_ssn" in _codes(r)
    dumped = json.dumps(r.model_dump(mode="json"))
    assert "123-45-6789" not in dumped and "123456789" not in dumped


def test_unreadable_box_escalates_instead_of_guessing():
    r = _build(box1_wages=None, unreadable_boxes=["Box 1"])
    assert "box1" not in r.fields and "unreadable_box" in _codes(r) and r.status == "needs_review"


def test_blank_box2_asks_for_confirmation_but_does_not_block():
    r = _build(box2_federal_withheld=None)
    assert r.status == "extracted" and "no_federal_withholding" in _codes(r)


def test_garbled_amount_is_flagged_not_fixed():
    r = _build(box1_wages="18,5OO.00")
    assert "box1" not in r.fields and "amount_not_a_number" in _codes(r)


def test_prior_year_escalates():
    r = _build(tax_year="2024")
    assert "wrong_tax_year" in _codes(r) and r.status == "needs_review"


def test_withholding_above_wages_is_flagged():
    r = _build(box2_federal_withheld="20,000.00")
    assert "withholding_exceeds_wages" in _codes(r)
    assert r.fields["box2"].value == "20000.00"  # flagged, not changed


def test_amounts_plug_straight_into_tax_math():
    from tax_math.primitives import sum_amounts

    r = _build()
    item = {"label": r.fields["box1"].source_label, "amount": r.fields["box1"].value}
    assert item["label"] == "W-2 w2-1 (Test Employer) box 1"
    result = sum_amounts([item, {**item, "label": "W-2 w2-2 (Other) box 1"}])
    assert result.value == 37000
