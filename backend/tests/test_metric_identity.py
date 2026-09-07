"""Metric identity must survive the ways documents label the same measurement.

These cases come from real extractions on the starter corpus: the earnings deck
labelled facts "FY24 EBITDA" and "FY23 EBITDA", which fragmented the blocking
key and pushed the same metric apart in vector space.
"""

from app.pipeline.normalize import canonical_claim, metric_key, strip_period_tokens


class TestStripPeriodTokens:
    def test_removes_fiscal_year_forms(self):
        assert strip_period_tokens(["fy24", "ebitda"]) == ["ebitda"]
        assert strip_period_tokens(["fy2024", "revenue"]) == ["revenue"]

    def test_removes_quarter_and_calendar(self):
        assert strip_period_tokens(["q4", "revenue"]) == ["revenue"]
        assert strip_period_tokens(["cy2023", "gdp"]) == ["gdp"]
        assert strip_period_tokens(["2024", "gdp"]) == ["gdp"]

    def test_keeps_real_metric_words_containing_digits(self):
        # "co2" and "g20" are metric words, not periods.
        assert strip_period_tokens(["co2", "emission"]) == ["co2", "emission"]
        assert strip_period_tokens(["g20", "average"]) == ["g20", "average"]


class TestMetricKeyAcrossPeriodLabels:
    def test_period_in_predicate_does_not_fragment_the_key(self):
        a = metric_key("Delhivery Limited", "FY24 EBITDA")
        b = metric_key("Delhivery Limited", "FY23 EBITDA")
        c = metric_key("Delhivery Limited", "EBITDA")
        assert a == b == c

    def test_quarter_label_collapses_too(self):
        assert metric_key("Delhivery", "Q4 FY24 revenue from services") == metric_key(
            "Delhivery", "revenue from services"
        )

    def test_genuinely_different_metrics_still_differ(self):
        assert metric_key("Delhivery", "FY24 EBITDA") != metric_key("Delhivery", "FY24 revenue")


class TestCanonicalClaimAcrossPeriodLabels:
    def test_embedding_text_is_period_free(self):
        claim = canonical_claim("Delhivery Limited", "FY24 EBITDA", ["consolidated"])
        assert "fy24" not in claim.lower()
        assert "ebitda" in claim.lower()

    def test_same_metric_different_years_embeds_identically(self):
        # This is what lets a same-metric/different-period pair reach the judge
        # and be labelled reconcilable-by-time instead of being missed entirely.
        assert canonical_claim("Delhivery", "FY24 EBITDA", []) == canonical_claim(
            "Delhivery", "FY23 EBITDA", []
        )

    def test_qualifiers_still_separate_scopes(self):
        standalone = canonical_claim("Delhivery", "EBITDA", ["standalone"])
        consolidated = canonical_claim("Delhivery", "EBITDA", ["consolidated"])
        assert standalone != consolidated
