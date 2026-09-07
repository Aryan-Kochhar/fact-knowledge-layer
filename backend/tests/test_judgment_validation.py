"""The guard against a model verdict that contradicts the arithmetic.

Motivating failure, observed on the starter corpus: a fact of 6.6% for 2023 and
a fact of 5.7% for 2024 were labelled `corroborates`, because both evidence
quotes happened to contain the phrase "5.7 per cent in 2024". The deterministic
layer only sees normalised values and periods, so it cannot be misled that way.
"""

from app.pipeline.linking import PairAnalysis, validate_judgment


def analysis(**kw) -> PairAnalysis:
    base = {
        "numeric": "equal",
        "period": "same",
        "basis": "same",
        "scope_overlap": "same",
        "relative_difference": 0.0,
    }
    base.update(kw)
    return PairAnalysis(**base)


class TestCorroboratesGuard:
    def test_flags_corroboration_across_different_periods(self):
        problems = validate_judgment("corroborates", analysis(period="different"))
        assert problems
        assert "different normalised periods" in problems[0]

    def test_flags_corroboration_with_differing_values(self):
        problems = validate_judgment(
            "corroborates", analysis(numeric="different", relative_difference=0.15)
        )
        assert problems
        assert "15.0%" in problems[0]

    def test_clean_corroboration_passes(self):
        assert validate_judgment("corroborates", analysis()) == []


class TestContradictsGuard:
    def test_flags_contradiction_across_periods(self):
        # Two different years can both be true - that is not a contradiction.
        problems = validate_judgment("contradicts", analysis(numeric="different", period="different"))
        assert any("different periods" in p for p in problems)

    def test_flags_contradiction_when_values_agree(self):
        problems = validate_judgment("contradicts", analysis(numeric="equal"))
        assert any("values agree" in p for p in problems)

    def test_flags_contradiction_across_unit_mismatch(self):
        problems = validate_judgment("contradicts", analysis(numeric="unit_mismatch"))
        assert any("different units" in p for p in problems)

    def test_flags_contradiction_between_estimate_and_actual(self):
        problems = validate_judgment(
            "contradicts", analysis(numeric="different", basis="different")
        )
        assert any("estimate/projection" in p for p in problems)

    def test_genuine_contradiction_passes(self):
        # same period, same basis, same units, values differ -> nothing to flag
        assert validate_judgment("contradicts", analysis(numeric="different")) == []


class TestOtherLabelsAreNotPoliced:
    def test_reconcilable_context_is_always_allowed(self):
        # This label exists precisely to cover differing periods, units and scopes.
        assert validate_judgment("reconcilable_context", analysis(period="different")) == []
        assert validate_judgment("reconcilable_context", analysis(numeric="different")) == []

    def test_unrelated_is_always_allowed(self):
        assert validate_judgment("unrelated", analysis(numeric="different")) == []
