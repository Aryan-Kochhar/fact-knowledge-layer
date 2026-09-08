"""Matching rules for the evaluation harness.

Kept apart from the report so they can be unit-tested. Every rule here is
deterministic: no model is asked whether two things mean the same, because a
scoring function that calls the system under test is not a measurement.

The metric rule is deliberately generous and the value rule deliberately strict.
A label names a metric in a human's words ("revenue from services") while the
pipeline names it in the document's ("Revenue for services (A)"), so demanding
an exact string would measure phrasing, not extraction. The number, on the other
hand, is the thing being claimed - it either matches or the fact is wrong.
"""

from __future__ import annotations

import re

# Words that carry no discriminating power in a financial metric name. Dropping
# them stops "revenue from services" and "revenue for services" from being
# treated as different metrics.
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on",
        "per", "the", "to", "with", "s",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def digits(text: object) -> str:
    """Every digit in the input, in order. '1,266.41' -> '126641'."""
    return re.sub(r"\D", "", str(text or ""))


def tokens(text: object) -> set[str]:
    """Content words, lowercased, stopwords dropped."""
    return {t for t in _TOKEN_RE.findall(str(text or "").lower()) if t not in _STOPWORDS}


def metric_overlap(label_metric: str, subject: str, predicate: str, qualifiers: str) -> float:
    """Fraction of the label's content words the fact's naming also uses."""
    wanted = tokens(label_metric)
    if not wanted:
        return 0.0
    have = tokens(subject) | tokens(predicate) | tokens(qualifiers)
    return len(wanted & have) / len(wanted)


def metric_matches(label_metric: str, subject: str, predicate: str, qualifiers: str) -> bool:
    """True when the pipeline's naming of a metric plausibly means the label's.

    Requires that most of the label's content words appear somewhere in the
    fact's subject, predicate or qualifiers. Two thirds rather than all of them,
    because a label says "revenue from express parcel services" where a document
    may say "Revenues from express parcel" and put the rest in a column header.
    """
    return metric_overlap(label_metric, subject, predicate, qualifiers) >= 2 / 3


def value_matches(label: dict, value_raw: str, value_num: float | None) -> bool:
    """True when the fact carries the number the label claims.

    Two independent ways to agree, because a fact can be right in the document's
    own units without having been normalised, and can be normalised correctly
    from a differently-punctuated original.
    """
    canonical = label.get("canonical_value")
    if canonical is not None and value_num is not None and _close(value_num, canonical):
        return True

    written = digits(label["value_text"])
    if not written:
        return False
    if written != digits(value_raw):
        return False

    # Digits alone lose the sign, so a bracketed loss must not match a profit.
    return _sign(label["value_text"]) == _sign(value_raw)


def normalisation_correct(label: dict, value_num: float | None) -> bool | None:
    """Did the normaliser land on the expected base-unit magnitude?

    None when the label does not assert one, so "not checked" stays distinct
    from "checked and wrong" in the report.
    """
    canonical = label.get("canonical_value")
    if canonical is None:
        return None
    if value_num is None:
        return False
    return _close(value_num, canonical)


def period_correct(label: dict, period_key: str | None) -> bool | None:
    """Compare canonical period keys. None when the label asserts no period."""
    expected = label.get("period")
    if expected is None:
        return None
    return (period_key or "") == expected


def _close(got: float, expected: float, tolerance: float = 0.005) -> bool:
    """Relative comparison, so a rounded crore figure still matches a precise one.

    ₹127 Cr and ₹1,266.41 million are the same quantity to within 0.3%, and both
    are correct readings of their own documents. That sets the floor: the bound
    cannot go below about 0.3% without rejecting a correct cross-document match,
    so 0.5% is as tight as this can usefully be.

    Which means tolerance alone cannot separate two genuinely different readings
    that happen to be close - an exchange rate of 85.62 sits 0.02% from a label
    asking for 85.6. Only the page rule in run_eval.py rejects that one.
    """
    if expected == 0:
        return abs(got) < 1e-9
    return abs(got - expected) / abs(expected) <= tolerance


def _sign(text: object) -> int:
    s = str(text or "").strip()
    negative = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    return -1 if negative else 1
