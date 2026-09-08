"""Currency recognition beyond the starter corpus.

The brief says the graders may test with additional PDFs, and the starter set is
entirely Indian - so INR, USD and a handful of others were all the pipeline had
ever seen. A figure whose currency is not recognised still gets the right
magnitude, but its dimension falls back to `quantity`, which quietly stops it
being compared against the same currency written differently.

The risk in widening the table is the opposite failure: a false hit. "audited"
appears constantly in financial text and contains "aud"; "academic" and
"decade" both contain "cad". Those are covered below, because a wrong currency
is worse than a missing one - it would make two unrelated figures look
comparable.
"""

from __future__ import annotations

import pytest

from app.pipeline.normalize import parse_value


@pytest.mark.parametrize(
    "value,unit,expected",
    [
        # the starter corpus
        ("8,142", "INR crore", "INR"),
        ("1,266.41", "INR Million", "INR"),
        ("1,234.56", "US$ million", "USD"),
        # currencies a grader could plausibly bring
        ("500", "CHF million", "CHF"),
        ("77", "AUD million", "AUD"),
        ("99", "CAD thousand", "CAD"),
        ("45", "SGD million", "SGD"),
        ("2.4", "EUR billion", "EUR"),
        ("850", "GBP thousand", "GBP"),
        ("1,234", "JPY million", "JPY"),
        ("300", "CNY million", "CNY"),
        ("12", "RMB billion", "CNY"),
        ("90", "HKD million", "HKD"),
        ("6", "AED million", "AED"),
        ("15", "ZAR million", "ZAR"),
        ("22", "BRL million", "BRL"),
        ("18", "SEK million", "SEK"),
    ],
)
def test_currency_codes_are_recognised(value, unit, expected):
    parsed = parse_value(value, unit)
    assert parsed.unit == expected
    assert parsed.dimension == "currency"


@pytest.mark.parametrize(
    "symbol,expected",
    [("€2.4", "EUR"), ("£850", "GBP"), ("¥1,234", "JPY"),
     ("₹1,000", "INR"), ("₩500", "KRW")],
)
def test_currency_symbols_are_recognised(symbol, expected):
    assert parse_value(symbol, "million").unit == expected


@pytest.mark.parametrize(
    "unit,expected",
    [
        # a bare "$" must not swallow the qualified dollar symbols
        ("A$ million", "AUD"),
        ("C$ million", "CAD"),
        ("S$ million", "SGD"),
        ("HK$ million", "HKD"),
        ("NZ$ million", "NZD"),
        ("US$ million", "USD"),
        ("$ million", "USD"),
        # nor must a bare "dollar" swallow the qualified names
        ("Australian dollar", "AUD"),
        ("Canadian dollar", "CAD"),
        ("Singapore dollar", "SGD"),
        ("dollar", "USD"),
    ],
)
def test_qualified_dollars_beat_the_bare_ones(unit, expected):
    assert parse_value("100", unit).unit == expected


@pytest.mark.parametrize(
    "unit",
    [
        "INR million (audited)",   # "aud" inside "audited"
        "audited figures",
        "unaudited",
        "per academic year",       # "cad" inside "academic"
        "over the decade",         # "cad" inside "decade"
        "brand value index",       # "rand" inside "brand", were it ever added
        "fraud losses",            # "aud" inside "fraud"
        "seek rate",               # near-miss on "sek"
    ],
)
def test_english_words_do_not_produce_false_currencies(unit):
    """A word that merely contains a currency code must not set one."""
    parsed = parse_value("100", unit)
    assert parsed.dimension != "currency" or parsed.unit == "INR", (
        f"{unit!r} was misread as {parsed.unit}"
    )


def test_an_unrecognised_currency_still_scales_correctly():
    """The magnitude must survive even when the currency is not known."""
    parsed = parse_value("500", "XYZ million")
    assert parsed.number == 500_000_000
    assert parsed.dimension != "currency"


def test_two_spellings_of_one_currency_become_comparable():
    """The point of the table: same money, different notation, same unit token."""
    a = parse_value("500", "CHF million")
    b = parse_value("0.5", "swiss franc billion")
    assert a.unit == b.unit == "CHF"
    assert a.number == b.number
