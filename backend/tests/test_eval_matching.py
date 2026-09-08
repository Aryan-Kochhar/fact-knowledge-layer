"""The evaluation harness scores the pipeline, so its own rules need testing.

A scorer that is too generous inflates the result it reports, which is worse
than not measuring at all. Both failure directions are covered here: matches
that must be accepted despite different wording, and near-misses that must be
rejected despite looking close.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.matching import (
    digits,
    metric_matches,
    metric_overlap,
    normalisation_correct,
    period_correct,
    tokens,
    value_matches,
)

LABELS = Path(__file__).resolve().parent.parent / "eval" / "labeled_facts.json"


# --------------------------------------------------------------------------
# tokenising
# --------------------------------------------------------------------------

def test_digits_keeps_order_and_drops_formatting():
    assert digits("1,266.41") == "126641"
    assert digits("(452)") == "452"
    assert digits("₹8,142 Cr") == "8142"
    assert digits(None) == ""


def test_tokens_drop_stopwords():
    assert tokens("revenue from services") == {"revenue", "services"}
    assert tokens("Revenue for services (A)") == {"revenue", "services", "a"} - {"a"}


# --------------------------------------------------------------------------
# metric naming
# --------------------------------------------------------------------------

def test_metric_matches_across_phrasing():
    # the label says "from", the document says "for"
    assert metric_matches("revenue from services", "Delhivery", "Revenue for services (A)", "[]")


def test_metric_matches_when_scope_sits_in_qualifiers():
    assert metric_matches(
        "revenue from express parcel services",
        "Delhivery Limited",
        "Revenues from express parcel",
        '["services", "consolidated"]',
    )


def test_metric_rejects_a_different_metric():
    assert not metric_matches(
        "personal loans growth", "India", "net outward foreign direct investment", "[]"
    )


def test_metric_rejects_partial_overlap_below_threshold():
    # one word of three is not a match, even though "reserves" is shared
    assert not metric_matches("gross reserves ratio", "India", "Official FX reserves", "[]")


def test_metric_overlap_is_zero_for_unrelated_names():
    assert metric_overlap("personal loans growth", "India", "foreign direct investment", "[]") == 0.0


def test_metric_overlap_is_positive_for_a_renaming():
    assert metric_overlap("gross reserves", "India", "Official FX reserves", "[]") > 0


# --------------------------------------------------------------------------
# values
# --------------------------------------------------------------------------

def _label(**kw):
    base = {"value_text": "127", "canonical_value": 1270000000.0}
    base.update(kw)
    return base


def test_value_matches_across_scale_when_canonical_agrees():
    # ₹127 Cr in the deck, ₹1,266.41 million in the annual report
    assert value_matches(_label(), "1,266.41", 1266410000.0)


def test_value_matches_on_written_digits_without_normalisation():
    assert value_matches(_label(canonical_value=None), "127", None)


def test_value_rejects_a_sign_flip():
    label = _label(value_text="(452)", canonical_value=-4520000000.0)
    assert value_matches(label, "(452)", -4520000000.0)
    assert not value_matches(label, "452", 4520000000.0)


def test_value_rejects_a_near_miss_outside_tolerance():
    label = _label(value_text="646.4", canonical_value=646400000000.0)
    assert not value_matches(label, "650.0", 650000000000.0)


def test_value_cannot_separate_two_close_readings():
    """Documents the limit of the value rule, so nobody trusts it too far.

    ₹85.6 and ₹85.62 per dollar are readings six months apart, but they sit 0.02%
    apart - inside any tolerance loose enough to accept a correct crore/million
    match. Rejecting this pair is the page rule's job, not this function's.
    """
    label = _label(value_text="85.6", canonical_value=85.6)
    assert value_matches(label, "85.62", 85.62)


def test_value_accepts_rounding_inside_tolerance():
    label = _label(value_text="8,594", canonical_value=85940000000.0)
    assert value_matches(label, "85,942.34", 85942340000.0)


# --------------------------------------------------------------------------
# derived-field checks
# --------------------------------------------------------------------------

def test_period_correct_requires_exact_key():
    assert period_correct({"period": "FY2024"}, "FY2024")
    assert not period_correct({"period": "FY2024"}, "@2024-03-31")


def test_period_check_is_skipped_when_the_label_asserts_none():
    assert period_correct({"period": None}, "FY2024") is None


def test_normalisation_check_distinguishes_unchecked_from_wrong():
    assert normalisation_correct({"canonical_value": None}, 5.0) is None
    assert normalisation_correct({"canonical_value": 1270000000.0}, None) is False
    assert normalisation_correct({"canonical_value": 1270000000.0}, 1266410000.0) is True


# --------------------------------------------------------------------------
# the labelled set itself
# --------------------------------------------------------------------------

def test_labels_are_well_formed():
    labels = json.loads(LABELS.read_text(encoding="utf-8"))["facts"]
    assert len(labels) >= 30

    seen = set()
    for label in labels:
        for field in ("id", "document", "page_index", "metric", "value_text", "difficulty"):
            assert label.get(field) not in (None, ""), f"{label.get('id')} missing {field}"
        assert label["id"] not in seen, f"duplicate id {label['id']}"
        seen.add(label["id"])
        assert label["difficulty"] in {"easy", "medium", "hard"}
        assert digits(label["value_text"]), f"{label['id']} has no digits in value_text"


def test_labels_span_every_starter_document():
    labels = json.loads(LABELS.read_text(encoding="utf-8"))["facts"]
    assert len({label["document"] for label in labels}) == 6


@pytest.mark.parametrize("label", json.loads(LABELS.read_text(encoding="utf-8"))["facts"])
def test_each_label_value_appears_in_its_own_source_text(label):
    """A label whose number is not in the sentence it was read from is a typo."""
    assert digits(label["value_text"]) in digits(label["source_text"]), label["id"]
