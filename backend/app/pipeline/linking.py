"""Finding and judging relationships between facts.

Three stages, cheapest first:

1. **Candidate generation** - vector search over canonical claim text (which
   excludes values and periods, so same-metric/different-value pairs are pulled
   *together* rather than apart), unioned with exact metric-key blocking to
   catch pairs the encoder misses.

2. **Deterministic analysis** - unit conversion, numeric comparison, period
   comparison, scope diffing. This costs nothing, is fully auditable, discards
   pairs that cannot relate, and is handed to the judge as computed evidence so
   the model reasons about a comparison rather than performing arithmetic.

3. **LLM judgment** - batched, on what survives. The model assigns the label and
   writes the reasoning a human will read.

Stage 2 exists because stage 3 is the expensive, rate-limited, non-deterministic
one. Every pair stage 2 can resolve or reject is quota we keep.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any

from ..config import settings
from ..db import new_id, now_iso, query
from ..llm import prompts
from ..llm.gemini import generate_json
from .embeddings import VectorIndex
from .normalize import fiscal_year_end

log = logging.getLogger("fkl.link")

VALID_RELATIONS = {"corroborates", "contradicts", "reconcilable_context", "unrelated"}
VALID_DIMENSIONS = {"time", "unit", "scope", "basis", "none"}

# Scale factors that show up when two documents state the same quantity at
# different magnitudes (crore vs million, percent vs fraction, ...).
_KNOWN_SCALE_FACTORS = {
    10.0: "10x",
    100.0: "100x (percent vs fraction, or lakh vs crore step)",
    1000.0: "1,000x (thousand)",
    1e5: "100,000x (lakh)",
    1e6: "1,000,000x (million)",
    1e7: "10,000,000x (crore)",
    1e9: "1,000,000,000x (billion)",
}


@dataclass
class PairAnalysis:
    """What the deterministic layer can say about a pair on its own."""

    numeric: str = "non_numeric"          # equal | close | different | unit_mismatch | non_numeric
    relative_difference: float | None = None
    scale_factor: str | None = None
    period: str = "unknown"               # same | aligned | different | one_missing | both_missing
    basis: str = "unknown"                # same | different
    scope_overlap: str = "unknown"        # same | differing
    differing_qualifiers: list[str] = field(default_factory=list)
    skip: str | None = None               # set => not worth an LLM call
    notes: list[str] = field(default_factory=list)

    def as_hint(self) -> dict[str, Any]:
        return {
            "numeric_comparison": self.numeric,
            "relative_difference": (
                None if self.relative_difference is None else round(self.relative_difference, 4)
            ),
            "possible_scale_factor": self.scale_factor,
            "period_comparison": self.period,
            "basis_comparison": self.basis,
            "scope_comparison": self.scope_overlap,
            "qualifiers_only_on_one_side": self.differing_qualifiers,
            "notes": self.notes,
        }


def _instant_on_fiscal_boundary(instant_key: str, fy_key: str) -> bool:
    """Does '@2024-03-31' fall exactly on the closing date of 'FY2024'?"""
    if not instant_key.startswith("@") or not fy_key.startswith("FY"):
        return False
    try:
        return instant_key[1:] == fiscal_year_end(int(fy_key[2:]))
    except (TypeError, ValueError):
        return False


def compare_periods(pa: str | None, pb: str | None) -> str:
    """same | aligned | different | one_missing | both_missing.

    `aligned` exists for a real pattern in financial statements: a table headed
    "for the year ended March 31, 2024" whose rows the model timestamps as the
    bare date "March 31, 2024". That normalises to an instant, while the same
    figure quoted in an earnings deck normalises to FY2024. The two are not
    literally the same key, but they describe the same reporting boundary, so
    treating them as flatly different would flag correct corroborations as
    inconsistent - which is exactly what happened on the starter corpus.

    It is kept distinct from `same` rather than merged into it, because a stock
    measured *at* a date and a flow measured *over* the year ending on that date
    genuinely are different things; the judge should still see the distinction.
    """
    if not pa and not pb:
        return "both_missing"
    if not pa or not pb:
        return "one_missing"
    if pa == pb:
        return "same"
    if _instant_on_fiscal_boundary(pa, pb) or _instant_on_fiscal_boundary(pb, pa):
        return "aligned"
    return "different"


def _qualifiers(fact: dict[str, Any]) -> set[str]:
    raw = fact.get("qualifiers")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = []
    if not isinstance(raw, list):
        return set()
    return {str(q).strip().lower() for q in raw if str(q).strip()}


def analyse_pair(a: dict[str, Any], b: dict[str, Any]) -> PairAnalysis:
    """Everything we can determine about a pair without asking a model."""
    out = PairAnalysis()

    a_num, b_num = a.get("value_num"), b.get("value_num")
    a_unit, b_unit = a.get("value_unit"), b.get("value_unit")
    a_dim, b_dim = a.get("value_dim"), b.get("value_dim")

    # --- numbers ---
    if a_num is None or b_num is None:
        out.numeric = "non_numeric"
    elif a_unit != b_unit:
        out.numeric = "unit_mismatch"
        out.notes.append(f"units differ after normalisation: {a_unit} vs {b_unit}")
    else:
        biggest = max(abs(a_num), abs(b_num))
        if biggest == 0:
            out.numeric = "equal"
            out.relative_difference = 0.0
        else:
            rel = abs(a_num - b_num) / biggest
            out.relative_difference = rel
            if rel <= settings.numeric_tolerance:
                out.numeric = "equal"
            elif rel <= 0.05:
                out.numeric = "close"
            else:
                out.numeric = "different"

        # A clean power-of-ten ratio is the fingerprint of a scale mismatch that
        # normalisation did not catch - worth telling the judge about.
        if a_num and b_num and out.numeric == "different":
            ratio = max(abs(a_num), abs(b_num)) / min(abs(a_num), abs(b_num))
            for factor, label in _KNOWN_SCALE_FACTORS.items():
                if math.isclose(ratio, factor, rel_tol=0.02):
                    out.scale_factor = label
                    out.notes.append(f"values differ by almost exactly {label}")
                    break

    # --- periods ---
    out.period = compare_periods(a.get("period_key"), b.get("period_key"))
    if out.period == "aligned":
        out.notes.append(
            "one period is a point-in-time on the other's fiscal year end; "
            "same reporting boundary, but a stock and a flow are not the same measure"
        )

    # --- basis ---
    ba, bb = a.get("period_basis") or "unknown", b.get("period_basis") or "unknown"
    out.basis = "same" if ba == bb else "different"

    # --- scope ---
    qa, qb = _qualifiers(a), _qualifiers(b)
    if qa or qb:
        symmetric = (qa - qb) | (qb - qa)
        out.scope_overlap = "same" if not symmetric else "differing"
        out.differing_qualifiers = sorted(symmetric)[:8]

    # --- rejection ---
    # Comparing money to a percentage is not a reconciliation problem, it is a
    # different measurement. Spending a call on it teaches us nothing.
    if a_dim and b_dim and a_dim != b_dim and {a_dim, b_dim} <= {"currency", "ratio", "count", "quantity", "rate_delta"}:
        out.skip = f"incomparable value dimensions ({a_dim} vs {b_dim})"

    return out


# --------------------------------------------------------------------------
# candidate generation
# --------------------------------------------------------------------------

def _pair_key(a_id: str, b_id: str) -> tuple[str, str]:
    return (a_id, b_id) if a_id <= b_id else (b_id, a_id)


def build_candidates(
    new_facts: list[dict[str, Any]],
    index: VectorIndex,
    vectors: dict[str, Any],
    fact_lookup: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pair each new fact with existing facts worth comparing it to."""
    new_ids = {f["id"] for f in new_facts}
    seen: set[tuple[str, str]] = set()
    candidates: list[dict[str, Any]] = []

    # --- vector recall ---
    for fact in new_facts:
        vector = vectors.get(fact["id"])
        if vector is None:
            continue
        hits = index.search(
            vector,
            top_k=settings.max_candidates_per_fact,
            threshold=settings.similarity_threshold,
            exclude_fact_ids={fact["id"]},
        )
        for other_id, score in hits:
            other = fact_lookup.get(other_id)
            if other is None:
                continue
            # Intra-document pairs are handled by the exact-blocking pass below;
            # letting them through here would flood the queue with near-duplicate
            # restatements from the same report.
            if other["doc_id"] == fact["doc_id"]:
                continue
            key = _pair_key(fact["id"], other_id)
            if key in seen:
                continue
            seen.add(key)
            candidates.append({"a": fact, "b": other, "similarity": score})

    # --- exact metric blocking (recall safety net) ---
    # Catches pairs the encoder scores below threshold because the two documents
    # word the same metric very differently.
    by_metric: dict[str, list[dict[str, Any]]] = {}
    for fact in fact_lookup.values():
        if fact.get("metric_key"):
            by_metric.setdefault(fact["metric_key"], []).append(fact)

    for fact in new_facts:
        for other in by_metric.get(fact.get("metric_key") or "", []):
            if other["id"] == fact["id"]:
                continue
            same_doc = other["doc_id"] == fact["doc_id"]
            if same_doc:
                # Only worth judging inside one document when it looks like an
                # internal inconsistency: same metric, same period, different value.
                if other["id"] in new_ids and other["id"] >= fact["id"]:
                    continue
                if not (fact.get("period_key") and fact.get("period_key") == other.get("period_key")):
                    continue
                if fact.get("value_num") is None or other.get("value_num") is None:
                    continue
                biggest = max(abs(fact["value_num"]), abs(other["value_num"]))
                if biggest == 0 or abs(fact["value_num"] - other["value_num"]) / biggest <= settings.numeric_tolerance:
                    continue
            key = _pair_key(fact["id"], other["id"])
            if key in seen:
                continue
            seen.add(key)
            candidates.append({"a": fact, "b": other, "similarity": 1.0 if not same_doc else 0.99})

    return candidates


def priority(cand: dict[str, Any]) -> float:
    """How likely this pair is to produce a verdict worth reading.

    Judgment is the scarce resource, so it goes to pairs whose deterministic
    signature already suggests a real relationship. The ranking deliberately
    favours *disagreement*: two documents quietly agreeing is the common case
    and the least informative, whereas a same-period value gap is either a
    contradiction or a scope difference worth naming - both interesting.
    """
    a, b, analysis = cand["a"], cand["b"], cand["analysis"]
    score = float(cand["similarity"])

    # The metric names normalise to the same key - strong evidence they measure
    # the same thing, independent of the encoder's opinion.
    if a.get("metric_key") and a["metric_key"] == b.get("metric_key"):
        score += 3.0

    if analysis.numeric == "different":
        # Same period + same units + different numbers is the contradiction
        # signature. This is the single most valuable thing to spend a call on.
        score += 3.5 if analysis.period in ("same", "aligned") else 1.5
    elif analysis.numeric == "close":
        # A near-miss on the same period is the most genuinely ambiguous pair
        # there is, and worth ranking almost as highly as an outright gap.
        score += 3.0 if analysis.period in ("same", "aligned") else 1.0
    elif analysis.numeric == "equal":
        # Corroboration is more interesting when the two documents wrote it
        # differently - identical strings teach a reader nothing.
        score += 1.5
        if (a.get("value_raw") or "").strip().lower() != (b.get("value_raw") or "").strip().lower():
            score += 1.0
        if (a.get("unit_raw") or "") != (b.get("unit_raw") or ""):
            score += 1.0
    elif analysis.numeric == "unit_mismatch":
        score += 1.0

    # A clean power-of-ten gap is almost always a scale-notation difference.
    if analysis.scale_factor:
        score += 2.0
    # Differing period, basis or scope are the reconcilable-by-context cases.
    if analysis.period == "different":
        score += 1.0
    if analysis.basis == "different":
        score += 1.0
    if analysis.scope_overlap == "differing":
        score += 0.75
    # Cross-document relationships are the point of the exercise.
    if a.get("doc_id") != b.get("doc_id"):
        score += 1.5
    # Evidence we could not verify should not drive a headline finding.
    if a.get("verification") == "unverified" or b.get("verification") == "unverified":
        score -= 2.0

    return score


def filter_candidates(
    candidates: list[dict[str, Any]],
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Split candidates into 'ask the model' and 'rejected deterministically'.

    Returns (to_judge, rejected_by_rules, dropped_for_budget).
    """
    keep: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for cand in candidates:
        analysis = analyse_pair(cand["a"], cand["b"])
        cand["analysis"] = analysis
        if analysis.skip:
            rejected.append(cand)
        else:
            cand["priority"] = priority(cand)
            keep.append(cand)

    keep.sort(key=lambda c: -c["priority"])

    cap = limit if limit is not None else settings.max_pairs_per_ingest
    dropped = 0
    if cap and len(keep) > cap:
        dropped = len(keep) - cap
        keep = keep[:cap]
    return keep, rejected, dropped


# --------------------------------------------------------------------------
# judgment
# --------------------------------------------------------------------------

def _clamp(value: Any, default: float = 0.5) -> float:
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return default


def validate_judgment(relation: str, analysis: PairAnalysis) -> list[str]:
    """Cross-check a model verdict against what the arithmetic already proved.

    The model sees each fact's evidence quote, and a quote often mentions figures
    and years *other* than the fact's own. Observed failure: a 6.6%-in-2023 fact
    and a 5.7%-in-2024 fact were labelled `corroborates` because both quotes
    happened to contain "5.7 per cent in 2024".

    The deterministic layer cannot be fooled that way - it only ever sees the
    normalised value and period. So where the label and the arithmetic disagree,
    we do not silently pick a winner: the relation is kept, its confidence is cut
    and it is flagged for review. Returns the reasons it looks wrong.
    """
    problems: list[str] = []

    if relation == "corroborates":
        # "aligned" is not a problem: an instant sitting on the other fact's
        # fiscal year end describes the same reporting boundary.
        if analysis.period == "different":
            problems.append(
                "labelled corroborates, but the two facts carry different normalised periods"
            )
        if analysis.numeric == "different":
            problems.append(
                f"labelled corroborates, but the normalised values differ by "
                f"{(analysis.relative_difference or 0) * 100:.1f}%"
            )
    elif relation == "contradicts":
        if analysis.period == "different":
            problems.append(
                "labelled contradicts, but the facts describe different periods, "
                "so both can be true"
            )
        if analysis.numeric == "equal":
            problems.append("labelled contradicts, but the normalised values agree")
        if analysis.numeric == "unit_mismatch":
            problems.append(
                "labelled contradicts, but the values are in different units and were "
                "never compared on a common basis"
            )
        if analysis.basis == "different":
            problems.append(
                "labelled contradicts, but one figure is an estimate/projection and the "
                "other is not"
            )

    return problems


def needs_escalation(cand: dict[str, Any]) -> bool:
    """Is this a pair where a weaker judge is likely to get it wrong?

    The expensive mistakes are all in one place: two figures that disagree
    numerically while everything else about them matches. Calling that a
    contradiction when a scope or basis difference explains it - or missing a
    real contradiction - is the costliest error the system can make, so those
    pairs get the stronger model. Pairs that plainly agree, or that differ on an
    obvious dimension like the year, do not need it.
    """
    analysis = cand["analysis"]
    # "close" (a fraction of a percent to 5% apart) is included deliberately, and
    # is arguably the hardest case of all: 6.4% against 6.5% for the same period
    # is either a data-vintage difference to be reconciled or a genuine conflict,
    # and nothing in the numbers alone decides which.
    if analysis.numeric not in ("different", "close"):
        return False
    return analysis.period in ("same", "aligned") or analysis.scale_factor is not None


async def judge_batch(batch: list[dict[str, Any]], model: str | None = None) -> list[dict[str, Any]]:
    """Ask the model to label one batch of pairs. Returns relation rows."""
    payload = []
    for i, cand in enumerate(batch):
        pair_id = f"p{i}"
        cand["pair_id"] = pair_id
        payload.append(
            {
                "pair_id": pair_id,
                "a": cand["a"],
                "b": cand["b"],
                "similarity": cand["similarity"],
                "computed": cand["analysis"].as_hint(),
            }
        )

    raw = await generate_json(
        prompts.judge_prompt(payload),
        system=prompts.JUDGE_SYSTEM,
        purpose="judge",
        temperature=0.0,
        max_output_tokens=32768,
        model=model or settings.gemini_judge_model,
    )

    if isinstance(raw, dict):
        for key in ("results", "pairs", "judgments", "data"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
    if not isinstance(raw, list):
        raise ValueError(f"judge returned {type(raw).__name__}, expected list")

    by_id = {c["pair_id"]: c for c in batch}
    rows: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        # Fall back to positional matching when the model omits or mangles pair_id.
        cand = by_id.get(str(item.get("pair_id"))) or (batch[i] if i < len(batch) else None)
        if cand is None:
            continue

        relation = str(item.get("relation") or "").strip().lower()
        if relation not in VALID_RELATIONS:
            relation = "unrelated"
        dimension = str(item.get("dimension") or "none").strip().lower()
        if dimension not in VALID_DIMENSIONS:
            dimension = "none"

        a_id, b_id = cand["a"]["id"], cand["b"]["id"]
        ordered_a, ordered_b = _pair_key(a_id, b_id)
        # _pair_key may swap the order; keep A/B consistent with what we asked about.
        if (ordered_a, ordered_b) != (a_id, b_id):
            ordered_a, ordered_b = a_id, b_id

        raw_confidence = _clamp(item.get("confidence"))
        confidence = raw_confidence
        problems = validate_judgment(relation, cand["analysis"])
        if problems:
            # Halve confidence and mark it, so a flagged verdict cannot lead the
            # showcase or be mistaken for a clean result. The model's own number
            # is kept alongside, so the penalty can be recomputed or lifted later
            # without re-asking (see scripts/revalidate.py).
            confidence = round(raw_confidence * 0.5, 3)

        rows.append(
            {
                "id": new_id("r_"),
                "fact_a": ordered_a,
                "fact_b": ordered_b,
                "cross_doc": int(cand["a"]["doc_id"] != cand["b"]["doc_id"]),
                "relation": relation,
                "reasoning": str(item.get("reasoning") or "").strip()[:2000] or "(no reasoning returned)",
                "reconciliation": (
                    str(item.get("reconciliation")).strip()[:1000]
                    if item.get("reconciliation") not in (None, "", "null")
                    else None
                ),
                "dimension": dimension,
                "confidence": confidence,
                "raw_confidence": raw_confidence,
                "similarity": float(cand["similarity"]),
                "decided_by": "llm-flagged" if problems else "llm",
                "created_at": now_iso(),
                # Consumed by the ingest layer, not a database column.
                "_problems": problems,
                "_pair": (cand["a"], cand["b"]),
            }
        )
    return rows


def load_facts(fact_ids: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """Facts joined with the document metadata the judge prompt needs."""
    sql = """SELECT f.*, d.filename, d.title AS doc_title
             FROM facts f JOIN documents d ON d.id = f.doc_id"""
    params: tuple = ()
    if fact_ids:
        placeholders = ",".join("?" for _ in fact_ids)
        sql += f" WHERE f.id IN ({placeholders})"
        params = tuple(fact_ids)
    return {row["id"]: row for row in query(sql, params)}
