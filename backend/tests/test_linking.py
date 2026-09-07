"""Deterministic pair analysis and the judgment-budget ranking."""

import json

from app.pipeline.linking import analyse_pair, filter_candidates, priority


def fact(**kw):
    base = {
        "id": kw.pop("id", "f1"),
        "doc_id": kw.pop("doc_id", "docA"),
        "subject": "Delhivery Limited",
        "predicate": "revenue from services",
        "value_raw": "8,142",
        "unit_raw": "INR crore",
        "value_num": 8142e7,
        "value_unit": "INR",
        "value_dim": "currency",
        "period_key": "FY2024",
        "period_basis": "actual",
        "metric_key": "delhivery|revenue-service",
        "qualifiers": json.dumps(["consolidated"]),
        "verification": "verified",
    }
    base.update(kw)
    return base


class TestAnalysePair:
    def test_same_number_different_notation_is_equal(self):
        a = fact(value_raw="8,142", unit_raw="INR crore", value_num=8142e7)
        b = fact(id="f2", doc_id="docB", value_raw="81.42", unit_raw="INR billion", value_num=81.42e9)
        result = analyse_pair(a, b)
        assert result.numeric == "equal"
        assert result.skip is None

    def test_differing_numbers_flagged(self):
        a = fact(value_num=8142e7)
        b = fact(id="f2", doc_id="docB", value_num=7225e7)
        assert analyse_pair(a, b).numeric == "different"

    def test_power_of_ten_gap_detected(self):
        a = fact(value_num=8142e7)
        b = fact(id="f2", doc_id="docB", value_num=8142e6)
        result = analyse_pair(a, b)
        assert result.scale_factor is not None

    def test_period_comparison(self):
        a = fact(period_key="FY2024")
        b = fact(id="f2", doc_id="docB", period_key="FY2023")
        assert analyse_pair(a, b).period == "different"

    def test_scope_difference_surfaced(self):
        a = fact(qualifiers=json.dumps(["consolidated"]))
        b = fact(id="f2", doc_id="docB", qualifiers=json.dumps(["standalone"]))
        result = analyse_pair(a, b)
        assert result.scope_overlap == "differing"
        assert "standalone" in result.differing_qualifiers

    def test_money_vs_percentage_is_skipped(self):
        # Not a reconciliation problem - a different measurement entirely.
        a = fact(value_dim="currency")
        b = fact(id="f2", doc_id="docB", value_dim="ratio", value_unit="percent", value_num=12.7)
        assert analyse_pair(a, b).skip is not None

    def test_units_that_differ_after_normalisation(self):
        a = fact(value_unit="INR", value_dim="currency")
        b = fact(id="f2", doc_id="docB", value_unit="USD", value_dim="currency", value_num=1e9)
        assert analyse_pair(a, b).numeric == "unit_mismatch"


class TestPriority:
    def _scored(self, a, b, similarity=0.7):
        cand = {"a": a, "b": b, "similarity": similarity}
        cand["analysis"] = analyse_pair(a, b)
        return priority(cand)

    def test_same_period_value_gap_outranks_plain_agreement(self):
        # The contradiction signature must win the budget over quiet agreement.
        conflict = self._scored(
            fact(value_num=8142e7),
            fact(id="f2", doc_id="docB", value_num=5000e7),
        )
        agreement = self._scored(
            fact(value_num=8142e7),
            fact(id="f2", doc_id="docB", value_num=8142e7, value_raw="8,142"),
        )
        assert conflict > agreement

    def test_differently_worded_agreement_outranks_identical_strings(self):
        different_notation = self._scored(
            fact(value_raw="8,142", unit_raw="INR crore", value_num=8142e7),
            fact(id="f2", doc_id="docB", value_raw="81.42", unit_raw="INR billion", value_num=8142e7),
        )
        identical = self._scored(
            fact(value_raw="8,142", unit_raw="INR crore", value_num=8142e7),
            fact(id="f2", doc_id="docB", value_raw="8,142", unit_raw="INR crore", value_num=8142e7),
        )
        assert different_notation > identical

    def test_unverified_evidence_is_penalised(self):
        verified = self._scored(fact(), fact(id="f2", doc_id="docB"))
        unverified = self._scored(fact(), fact(id="f2", doc_id="docB", verification="unverified"))
        assert unverified < verified

    def test_cross_document_outranks_same_document(self):
        cross = self._scored(fact(), fact(id="f2", doc_id="docB"))
        same = self._scored(fact(), fact(id="f2", doc_id="docA"))
        assert cross > same


class TestFilterCandidates:
    def test_budget_cap_keeps_highest_priority(self):
        interesting = {
            "a": fact(value_num=8142e7),
            "b": fact(id="f2", doc_id="docB", value_num=5000e7),
            "similarity": 0.7,
        }
        dull = {
            "a": fact(id="f3", value_num=8142e7),
            "b": fact(id="f4", doc_id="docB", value_num=8142e7),
            "similarity": 0.7,
        }
        keep, rejected, dropped = filter_candidates([dull, interesting], limit=1)
        assert dropped == 1
        assert len(keep) == 1
        assert keep[0] is interesting
        assert rejected == []

    def test_rule_rejected_pairs_do_not_consume_budget(self):
        incomparable = {
            "a": fact(value_dim="currency"),
            "b": fact(id="f2", doc_id="docB", value_dim="ratio", value_unit="percent", value_num=12.0),
            "similarity": 0.9,
        }
        keep, rejected, dropped = filter_candidates([incomparable], limit=10)
        assert keep == []
        assert len(rejected) == 1
        assert dropped == 0
