from app.pipeline.normalize import (
    canonical_claim,
    detect_basis,
    metric_key,
    normalize_period,
    parse_value,
)


class TestParseValue:
    def test_plain_number(self):
        v = parse_value("1,234")
        assert v.number == 1234
        assert v.dimension == "count"

    def test_indian_crore_scale(self):
        v = parse_value("8,142", "INR crore")
        assert v.number == 8142 * 1e7
        assert v.unit == "INR"
        assert v.dimension == "currency"

    def test_crore_and_billion_agree(self):
        # The whole point of normalisation: these are the same money.
        a = parse_value("8,142", "INR crore")
        b = parse_value("81.42", "INR billion")
        assert abs(a.number - b.number) / a.number < 1e-9

    def test_rupee_symbol_inline(self):
        v = parse_value("₹2,076 Cr")
        assert v.unit == "INR"
        assert v.number == 2076 * 1e7

    def test_usd_billion(self):
        v = parse_value("US$ 3.1 billion")
        assert v.unit == "USD"
        assert v.number == 3.1e9

    def test_percent_variants(self):
        for raw, unit in [("6.5%", None), ("6.5", "per cent"), ("6.5", "percent")]:
            v = parse_value(raw, unit)
            assert v.number == 6.5
            assert v.unit == "percent"
            assert v.dimension == "ratio"

    def test_basis_points_become_percentage_points(self):
        v = parse_value("25", "bps")
        assert v.number == 0.25
        assert v.unit == "pp"

    def test_accounting_negative(self):
        v = parse_value("(1,234)", "INR crore")
        assert v.number == -1234 * 1e7

    def test_range_uses_midpoint_and_flags(self):
        v = parse_value("6.3-6.8", "per cent")
        assert v.is_range
        assert abs(v.number - 6.55) < 1e-9

    def test_scale_word_not_matched_inside_another_word(self):
        # "k" must not fire inside "risk", "cr" must not fire inside "increase"
        v = parse_value("42", "risk incidents")
        assert v.number == 42

    def test_categorical_value(self):
        v = parse_value("AAA/Stable", None)
        assert v.number is None
        assert v.dimension == "categorical"

    def test_lakh_scale(self):
        v = parse_value("2.5", "lakh tonnes")
        assert v.number == 2.5e5


class TestNormalizePeriod:
    def test_fy_short_form(self):
        p = normalize_period("FY24")
        assert p.key == "FY2024"
        assert p.start == "2023-04-01"
        assert p.end == "2024-03-31"

    def test_fy_long_form_matches_short(self):
        assert normalize_period("FY 2023-24").key == normalize_period("FY24").key

    def test_bare_hyphenated_year_is_fiscal(self):
        assert normalize_period("2023-24").key == "FY2024"
        assert normalize_period("2024-25").key == "FY2025"

    def test_imf_slash_convention(self):
        assert normalize_period("FY2024/25").key == "FY2025"

    def test_quarter(self):
        p = normalize_period("Q4 FY24")
        assert p.key == "Q4FY2024"
        assert p.start == "2024-01-01"

    def test_quarter_is_not_the_same_as_year(self):
        assert normalize_period("Q4 FY24").key != normalize_period("FY24").key

    def test_calendar_year_distinct_from_fiscal(self):
        assert normalize_period("calendar year 2024").key == "CY2024"
        assert normalize_period("2024").key == "CY2024"
        assert normalize_period("CY2024").key != normalize_period("FY2024").key

    def test_instant(self):
        p = normalize_period("as at March 31, 2024")
        assert p.key == "@2024-03-31"
        assert p.kind == "instant"

    def test_instant_day_first(self):
        assert normalize_period("as of 31 March 2024").key == "@2024-03-31"

    def test_month(self):
        assert normalize_period("December 2024").key == "2024-12"

    def test_half_year(self):
        assert normalize_period("H1 FY25").key == "H1FY2025"

    def test_empty_falls_back_to_document_context(self):
        p = normalize_period(None, fallback="FY2023-24")
        assert p.key == "FY2024"
        assert any("inherited" in n for n in p.notes)

    def test_unparseable_stays_none(self):
        assert normalize_period("sometime recently").key is None


class TestBasis:
    def test_model_value_respected(self):
        assert detect_basis("projection") == "projection"

    def test_text_cue_overrides_actual(self):
        assert detect_basis("actual", "GDP is projected to grow 6.5%") == "projection"

    def test_plain_actual(self):
        assert detect_basis("actual", "Revenue was 8,142 crore") == "actual"

    def test_unknown_default(self):
        assert detect_basis(None, "") == "unknown"


class TestMetricKey:
    def test_wording_variation_collapses(self):
        a = metric_key("Delhivery Limited", "revenue from operations")
        b = metric_key("Delhivery Limited", "the revenue from operations")
        assert a == b

    def test_plural_singular_collapse(self):
        assert metric_key("Delhivery", "shipments handled") == metric_key("Delhivery", "shipment handled")

    def test_different_metrics_differ(self):
        assert metric_key("Delhivery", "revenue") != metric_key("Delhivery", "net profit")


class TestCanonicalClaim:
    def test_value_and_period_excluded(self):
        # Vector search must surface same-metric/different-value pairs, so the
        # embedded text must not encode the value.
        claim = canonical_claim("Delhivery Limited", "revenue from operations", ["consolidated"])
        assert "8,142" not in claim
        assert "consolidated" in claim
        assert "revenue from operations" in claim
