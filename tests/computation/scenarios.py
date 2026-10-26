"""Shared test returns, worked by hand. Every SSN is in the 900-999 test range."""


def mfj_return() -> dict:
    """Married filing jointly, both 65+: a W-2, two SSA-1099s, a 1099-INT, an IRA 1099-R."""
    return {
        "intake_record": {
            "tax_year": 2025,
            "filing_status": "married_filing_jointly",
            "lived_apart_from_spouse_all_year": None,
            "taxpayer": {"ssn": "900-12-3456", "date_of_birth": "1958-03-02", "legally_blind": False},
            "spouse": {"ssn": "901-22-3333", "date_of_birth": "1960-07-19", "legally_blind": False},
            "dependents": [],
            "documents": [
                {"type": "W-2", "count": 1, "in_scope": True, "description": None},
                {"type": "SSA-1099", "count": 2, "in_scope": True, "description": None},
                {"type": "1099-INT", "count": 1, "in_scope": True, "description": None},
                {"type": "1099-R", "count": 1, "in_scope": True, "description": None},
            ],
            "estimated_payments": [{"amount": "1000.00"}, {"amount": "1000.00"}],
            "confirmed_by_taxpayer": True,
        },
        "intake_answers": {
            "spouse_died_in_2025": False,
            "can_be_claimed_as_dependent": False,
            "spouse_can_be_claimed_as_dependent": False,
            "dual_status_alien": False,
        },
        "extracted_documents": [{
            "document_id": "w2-1",
            "document_type": "W-2",
            "status": "extracted",
            "attempt": 1,
            "identifiers": {"employee_ssn_masked": "XXX-XX-3456", "ssn_check": "valid_test", "tax_year": "2025"},
            "fields": {
                "box1": {"value": "18500.00", "source_label": "W-2 #1 (Maple Grove Public Library) box 1"},
                "box2": {"value": "1850.00", "source_label": "W-2 #1 (Maple Grove Public Library) box 2"},
            },
        }],
        "entered_documents": [
            {"document_id": "ssa-1", "document_type": "SSA-1099", "recipient": "taxpayer", "tax_year": 2025,
             "boxes": {"5": "24000.00"}},
            {"document_id": "ssa-2", "document_type": "SSA-1099", "recipient": "spouse", "tax_year": 2025,
             "boxes": {"5": "16000.00"}},
            {"document_id": "int-1", "document_type": "1099-INT", "recipient": "taxpayer", "tax_year": 2025,
             "boxes": {"1": "1200.00", "8": "300.00"}},
            {"document_id": "r-1", "document_type": "1099-R", "recipient": "taxpayer", "tax_year": 2025,
             "boxes": {"1": "40000.00", "2a": "40000.00", "4": "4000.00", "7": "7",
                       "2b_taxable_not_determined": False, "ira_sep_simple": True}},
        ],
    }


# Worked by hand. 6b: provisional income 59,700 + 300 + 20,000 = 80,000; MFJ worksheet gives
# 6,000 + 85% x 36,000 = 36,600, capped at 85% x 40,000 = 34,000. Tax on 47,000 (Schedule Y-1):
# 2,385 + 12% x 23,150 = 5,163.
MFJ_EXPECTED = {
    "1z": 18500, "2a": 300, "2b": 1200, "3a": 0, "3b": 0, "4a": 40000, "4b": 40000, "5a": 0, "5b": 0,
    "6a": 40000, "6b": 34000, "7": 0, "9": 93700, "11a": 93700, "12e": 34700, "13b": 12000, "14": 46700,
    "15": 47000, "16": 5163, "18": 5163, "21": 0, "22": 5163, "24": 5163, "25a": 1850, "25b": 4000,
    "25c": 0, "25d": 5850, "26": 2000, "33": 7850, "34": 2687, "37": 0,
}
