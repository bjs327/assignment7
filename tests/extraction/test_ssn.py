from security import REDACTED, check_ssn, contains_ssn, is_test_ssn, mask_ssn, redact_ssns


def test_accepts_only_900_to_999():
    for ssn in ("900-12-3456", "901-55-1234", "999-99-9999", "900123456", "950 12 3456"):
        assert check_ssn(ssn) == "valid_test", ssn
    for ssn in ("123-45-6789", "899-99-9999", "078-05-1120", "000-00-0000"):
        assert check_ssn(ssn) == "not_test_range", ssn


def test_malformed_and_missing():
    assert check_ssn(None) == "missing"
    assert check_ssn("   ") == "missing"
    for bad in ("90-123-4567", "900-12-345", "9001234567", "900-12 3456", "abc-de-fghi"):
        assert check_ssn(bad) == "malformed", bad
    assert not is_test_ssn("900-12-345")


def test_mask_keeps_last_four_only_for_test_ssns():
    assert mask_ssn("900-12-3456") == "XXX-XX-3456"
    assert mask_ssn("123-45-6789") == REDACTED  # possibly real: reveal nothing
    assert mask_ssn("garbage") == REDACTED


def test_redact_free_text():
    text = "my number is 123-45-6789 and my wife's is 901 55 1234, account 1234567890123"
    out = redact_ssns(text)
    assert "123-45-6789" not in out and "45-6789" not in out
    assert "XXX-XX-1234" in out
    assert "1234567890123" in out  # long digit runs are not SSNs
    assert contains_ssn(text) and not contains_ssn("wages were 18,500.00")


def test_redact_leaves_ein_and_amounts_alone():
    assert redact_ssns("EIN 12-3456789, box 1 18500.00") == "EIN 12-3456789, box 1 18500.00"
