"""Escalation routing and the model-level circuit breaker.

Both come from a measured failure: the thinking model's free-tier daily
allowance ran out mid-run, every key returned 429 RESOURCE_EXHAUSTED for it, and
each judgment call burned three attempts rediscovering that before falling back.
"""

import json

from app.llm.gemini import _ModelHealth
from app.pipeline.linking import analyse_pair, needs_escalation


def fact(**kw):
    base = {
        "id": kw.pop("id", "f1"),
        "doc_id": kw.pop("doc_id", "docA"),
        "subject": "India",
        "predicate": "real GDP growth",
        "value_raw": "6.4",
        "unit_raw": "per cent",
        "value_num": 6.4,
        "value_unit": "percent",
        "value_dim": "ratio",
        "period_key": "FY2025",
        "period_basis": "actual",
        "metric_key": "india|gdp-growth-real",
        "qualifiers": json.dumps([]),
        "verification": "verified",
    }
    base.update(kw)
    return base


def candidate(a, b):
    return {"a": a, "b": b, "similarity": 0.9, "analysis": analyse_pair(a, b)}


class TestEscalationRouting:
    def test_same_period_disagreement_is_escalated(self):
        # The contradiction signature: everything matches except the number.
        cand = candidate(fact(value_num=6.4), fact(id="f2", doc_id="docB", value_num=6.5, value_raw="6.5"))
        assert needs_escalation(cand)

    def test_scale_factor_gap_is_escalated(self):
        cand = candidate(
            fact(value_num=8142e7, value_unit="INR", value_dim="currency"),
            fact(id="f2", doc_id="docB", value_num=8142e6, value_unit="INR", value_dim="currency"),
        )
        assert needs_escalation(cand)

    def test_agreement_is_not_escalated(self):
        cand = candidate(fact(value_num=6.4), fact(id="f2", doc_id="docB", value_num=6.4))
        assert not needs_escalation(cand)

    def test_plain_period_difference_is_not_escalated(self):
        # Different years with different values is the ordinary, easy case.
        cand = candidate(
            fact(value_num=6.4, period_key="FY2024"),
            fact(id="f2", doc_id="docB", value_num=7.2, period_key="FY2023"),
        )
        assert not needs_escalation(cand)

    def test_non_numeric_pair_is_not_escalated(self):
        cand = candidate(
            fact(value_num=None, value_dim="categorical", value_unit=None),
            fact(id="f2", doc_id="docB", value_num=None, value_dim="categorical", value_unit=None),
        )
        assert not needs_escalation(cand)


class TestModelHealth:
    def test_model_starts_available(self):
        health = _ModelHealth()
        assert health.is_available("gemini-x")

    def test_single_refusal_does_not_trip_the_breaker(self):
        # One key hitting a limit is not evidence the whole model is spent.
        health = _ModelHealth()
        health.record_quota_error("gemini-x", 0)
        assert health.is_available("gemini-x")

    def test_two_distinct_keys_trip_the_breaker(self):
        health = _ModelHealth()
        health.record_quota_error("gemini-x", 0)
        health.record_quota_error("gemini-x", 1)
        assert not health.is_available("gemini-x")

    def test_same_key_twice_does_not_trip_it(self):
        health = _ModelHealth()
        health.record_quota_error("gemini-x", 0)
        health.record_quota_error("gemini-x", 0)
        assert health.is_available("gemini-x")

    def test_breaker_is_scoped_to_one_model(self):
        health = _ModelHealth()
        health.record_quota_error("gemini-x", 0)
        health.record_quota_error("gemini-x", 1)
        assert not health.is_available("gemini-x")
        assert health.is_available("gemini-y")

    def test_success_clears_the_breaker(self):
        health = _ModelHealth()
        health.record_quota_error("gemini-x", 0)
        health.record_quota_error("gemini-x", 1)
        health.record_success("gemini-x")
        assert health.is_available("gemini-x")

    def test_snapshot_reports_remaining_cooldown(self):
        health = _ModelHealth()
        health.record_quota_error("gemini-x", 0)
        health.record_quota_error("gemini-x", 1)
        snap = health.snapshot()
        assert "gemini-x" in snap
        assert 0 < snap["gemini-x"] <= _ModelHealth.COOLDOWN_SECONDS
