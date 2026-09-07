"""An instant on a fiscal year end is neither the same period nor a different one.

Found by the judgment validator on the starter corpus. Financial-statement
tables are headed "for the year ended March 31, 2024" but the model timestamps
individual rows with the bare date, which normalises to an instant. The same
figure in an earnings deck normalises to FY2024. Treating those as flatly
different flagged 36 correct corroborations as inconsistent.
"""

from app.pipeline.linking import analyse_pair, compare_periods, validate_judgment


class TestComparePeriods:
    def test_identical_keys_are_same(self):
        assert compare_periods("FY2024", "FY2024") == "same"

    def test_instant_on_fiscal_year_end_is_aligned(self):
        assert compare_periods("@2024-03-31", "FY2024") == "aligned"
        assert compare_periods("FY2024", "@2024-03-31") == "aligned"

    def test_instant_on_the_wrong_fiscal_year_is_different(self):
        assert compare_periods("@2024-03-31", "FY2023") == "different"

    def test_mid_year_instant_is_different(self):
        assert compare_periods("@2024-09-30", "FY2024") == "different"

    def test_unrelated_periods_are_different(self):
        assert compare_periods("FY2024", "CY2024") == "different"
        assert compare_periods("Q4FY2024", "FY2024") == "different"

    def test_missing_keys(self):
        assert compare_periods(None, None) == "both_missing"
        assert compare_periods("FY2024", None) == "one_missing"

    def test_stub_period_is_not_aligned_with_anything(self):
        assert compare_periods("9M@2021-12-31", "FY2022") == "different"
        assert compare_periods("9M@2021-12-31", "@2021-12-31") == "different"


class TestValidatorRespectsAlignment:
    def _analysis(self, pa, pb):
        base = {
            "value_num": 1.0, "value_unit": "INR", "value_dim": "currency",
            "period_basis": "actual", "qualifiers": "[]",
        }
        return analyse_pair({**base, "period_key": pa}, {**base, "period_key": pb})

    def test_corroboration_across_aligned_periods_is_not_flagged(self):
        analysis = self._analysis("@2024-03-31", "FY2024")
        assert analysis.period == "aligned"
        assert validate_judgment("corroborates", analysis) == []

    def test_corroboration_across_genuinely_different_periods_is_still_flagged(self):
        analysis = self._analysis("FY2024", "FY2023")
        assert validate_judgment("corroborates", analysis) != []

    def test_alignment_is_explained_in_the_notes(self):
        analysis = self._analysis("@2024-03-31", "FY2024")
        assert any("fiscal year end" in note for note in analysis.notes)
