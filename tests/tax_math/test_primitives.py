import pytest

from tax_math import LabeledAmount, TaxMathInputError, subtract_amounts, sum_amounts


def item(label, amount):
    return {"label": label, "amount": amount}


class TestSumAmounts:
    def test_adds_and_rounds_the_total(self):
        result = sum_amounts([item("W-2 #1 box 1", "52345.67"), item("W-2 #2 box 1", "1200.50")])
        assert result.value == 53_546  # 53,546.17
        assert [step.label for step in result.steps] == ["W-2 #1 box 1", "W-2 #2 box 1", "Total", "Rounded to whole dollars"]

    def test_keeps_cents_until_the_total(self):
        # Rounding each amount first would give $0 + $0 = $0.
        assert sum_amounts([item("a", "0.30"), item("b", "0.30")]).value == 1

    def test_empty_list_totals_zero(self):
        result = sum_amounts([])
        assert result.value == 0
        assert result.summary == "Nothing to add, so the total is $0."

    def test_allows_negative_amounts(self):
        assert sum_amounts([item("Other income", "100"), item("Loss", "-30.25")]).value == 70  # 69.75

    def test_accepts_labeled_amount_objects(self):
        assert sum_amounts([LabeledAmount(label="x", amount="12.40")]).value == 12

    @pytest.mark.parametrize(
        "items, field",
        [
            ([{"amount": "5"}], "items[0]"),
            ([item("", "5")], "items[0]"),
            ([item("ok", "5"), item("too precise", "1.234")], "items[1].amount"),
            ("52345", "items"),
        ],
    )
    def test_rejects_bad_items(self, items, field):
        with pytest.raises(TaxMathInputError) as raised:
            sum_amounts(items)
        assert raised.value.field == field


class TestSubtractAmounts:
    def test_subtracts(self):
        result = subtract_amounts(item("Line 9", "60000"), item("Line 10", "2500"))
        assert result.value == 57_500
        assert result.summary == "Line 9 less Line 10: $57,500."

    def test_can_go_negative_by_default(self):
        assert subtract_amounts(item("Line 9", "20000"), item("Line 10", "37900")).value == -17_900

    def test_floor_at_zero(self):
        result = subtract_amounts(item("Line 11a", "20000"), item("Line 14", "37900"), floor_at_zero=True)
        assert result.value == 0
        assert result.steps[-1].label == "Not below zero"
        assert result.summary.endswith("(not below zero).")

    def test_overpaid_and_owed_are_never_both_positive(self):
        tax, payments = item("Line 24", "4200"), item("Line 33", "5000")
        overpaid = subtract_amounts(payments, tax, floor_at_zero=True)
        owed = subtract_amounts(tax, payments, floor_at_zero=True)
        assert (overpaid.value, owed.value) == (800, 0)

    def test_rounds_half_up(self):
        assert subtract_amounts(item("a", "100.50"), item("b", "0")).value == 101

    def test_floor_flag_must_be_boolean(self):
        with pytest.raises(TaxMathInputError) as raised:
            subtract_amounts(item("a", "1"), item("b", "2"), floor_at_zero="yes")
        assert raised.value.field == "floor_at_zero"
