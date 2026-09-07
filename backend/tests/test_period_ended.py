"""A period that ENDS on a date is not an instant.

Found by the judgment validator, which flagged 50 verdicts as inconsistent. On
inspection 46 of them were the model being right and the normaliser being wrong:
"for the year ended March 31, 2024" was collapsed to the instant @2024-03-31,
which made a year's EBITDA incomparable with the same figure written "FY24".
158 facts in the starter corpus were mislabelled this way.
"""

import pytest

from app.pipeline.normalize import normalize_period


class TestYearEndedIsAPeriod:
    @pytest.mark.parametrize(
        "text",
        [
            "for the year ended March 31, 2024",
            "For the year ended March 31, 2024",
            "year ended March 31, 2024",
            "year ending March 31, 2024",
            "for the year ended 31 March 2024",
        ],
    )
    def test_reads_as_the_fiscal_year(self, text):
        result = normalize_period(text)
        assert result.key == "FY2024", f"{text!r} -> {result.key}"
        assert result.kind == "fiscal_year"

    def test_matches_the_short_form_used_elsewhere(self):
        # The whole point: these must compare equal across documents.
        assert normalize_period("for the year ended March 31, 2024").key == normalize_period("FY24").key

    def test_previous_year(self):
        assert normalize_period("for the year ended March 31, 2023").key == "FY2023"

    def test_december_year_end_is_a_calendar_year(self):
        assert normalize_period("for the year ended December 31, 2024").key == "CY2024"


class TestAsAtIsStillAnInstant:
    @pytest.mark.parametrize(
        "text",
        ["as at March 31, 2024", "as of March 31, 2024", "as on March 31, 2024"],
    )
    def test_point_in_time_preserved(self, text):
        result = normalize_period(text)
        assert result.key == "@2024-03-31"
        assert result.kind == "instant"

    def test_balance_sheet_and_income_statement_do_not_collide(self):
        # A stock measured at a date and a flow measured over the year that ends
        # on that date are different things and must not compare equal.
        assert normalize_period("as at March 31, 2024").key != normalize_period(
            "for the year ended March 31, 2024"
        ).key


class TestStubAndSubAnnualPeriods:
    def test_nine_month_stub_is_neither_fy_nor_instant(self):
        result = normalize_period("nine months period ended December 31, 2021")
        assert result.key == "9M@2021-12-31"
        assert result.kind == "range"
        assert result.key != "CY2021"
        assert result.key != "@2021-12-31"

    def test_numeric_month_count(self):
        assert normalize_period("6 months ended September 30, 2024").kind == "half"

    def test_quarter_ended(self):
        result = normalize_period("quarter ended March 31, 2024")
        assert result.key == "Q4FY2024"
        assert result.kind == "quarter"

    def test_quarter_ended_june(self):
        # June is the first fiscal quarter under an April start.
        assert normalize_period("quarter ended June 30, 2024").key == "Q1FY2025"


class TestNoRegressions:
    def test_plain_forms_unchanged(self):
        assert normalize_period("FY24").key == "FY2024"
        assert normalize_period("2023-24").key == "FY2024"
        assert normalize_period("Q4 FY24").key == "Q4FY2024"
        assert normalize_period("December 2024").key == "2024-12"
        assert normalize_period("2024").key == "CY2024"

    def test_bare_date_without_ended_is_still_an_instant(self):
        assert normalize_period("March 31, 2024").key == "@2024-03-31"
