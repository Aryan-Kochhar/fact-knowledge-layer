"""Regression tests for date-vs-month period parsing.

The bug these lock down: "March 31, 2024" matched the month-year rule as
(month=March, year=31) and expanded to 2031-03. It affected 447 facts - 14% of
the corpus - because "as at March 31, 2024" is how every Indian balance-sheet
line states its date.
"""

import pytest

from app.pipeline.normalize import normalize_period


class TestFullDatesAreInstants:
    @pytest.mark.parametrize(
        "text",
        [
            "March 31, 2024",
            "as at March 31, 2024",
            "as of March 31, 2024",
            "31 March 2024",
            "as at 31 March 2024",
            "as on 31st March 2024",
            "Mar. 31, 2024",
        ],
    )
    def test_variants_all_resolve_to_the_same_instant(self, text):
        result = normalize_period(text)
        assert result.key == "@2024-03-31", f"{text!r} -> {result.key}"
        assert result.kind == "instant"

    def test_december_thirty_first(self):
        assert normalize_period("December 31, 2023").key == "@2023-12-31"

    def test_no_spurious_future_year(self):
        # The old bug produced 2031-03 / 2031-12 from day numbers.
        for text in ("March 31, 2024", "December 31, 2024", "January 31, 2025"):
            assert "2031" not in (normalize_period(text).key or "")

    def test_invalid_date_does_not_crash(self):
        # 31 February is not a date; fall through rather than raise.
        result = normalize_period("February 31, 2024")
        assert result.key != "@2024-02-31"


class TestMonthPeriodsStillWork:
    def test_month_with_four_digit_year(self):
        assert normalize_period("December 2024").key == "2024-12"

    def test_hyphenated_short_form(self):
        assert normalize_period("Dec-24").key == "2024-12"

    def test_as_at_month_only_is_an_instant(self):
        assert normalize_period("as at March 2024").key == "@2024-03-01"


class TestNoRegressionOnOtherForms:
    def test_fiscal_year_unaffected(self):
        assert normalize_period("FY24").key == "FY2024"
        assert normalize_period("2023-24").key == "FY2024"

    def test_quarter_unaffected(self):
        assert normalize_period("Q4 FY24").key == "Q4FY2024"

    def test_calendar_year_unaffected(self):
        assert normalize_period("2024").key == "CY2024"

    def test_quarter_with_embedded_date_prefers_quarter(self):
        assert normalize_period("Q4 FY24").kind == "quarter"
