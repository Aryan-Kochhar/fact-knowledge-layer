"""The four demonstration cases, computed from whatever is currently ingested.

The assignment asks for four things to be shown with evidence and reasoning:

  1. a fact corroborated across documents, stated differently
  2. a genuine or likely contradiction
  3. an apparent contradiction that context explains
  4. an extraction or reasoning failure, and what was done about it

Nothing here is hardcoded to the starter documents. Each case is a *query* over
the live store, ranked by how well an example demonstrates the case. Ingest a
different corpus and the same queries surface that corpus's examples - which is
the point, since the system has to work on unseen PDFs.
"""

from __future__ import annotations

import json
from typing import Any

from ..db import query

_FACT_COLUMNS = """
    f.id, f.doc_id, f.subject, f.predicate, f.value_raw, f.unit_raw, f.time_scope_raw,
    f.qualifiers, f.value_num, f.value_unit, f.value_dim, f.period_key, f.period_basis,
    f.claim_text, f.quote, f.page_index, f.printed_page, f.verification, f.verify_score,
    f.confidence, d.filename, d.title AS doc_title
"""


def _decode(fact: dict[str, Any]) -> dict[str, Any]:
    raw = fact.get("qualifiers")
    if isinstance(raw, str):
        try:
            fact["qualifiers"] = json.loads(raw)
        except json.JSONDecodeError:
            fact["qualifiers"] = []
    return fact


def hydrate_relations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach both full fact records (with document metadata) to relation rows."""
    if not rows:
        return []
    wanted = {r["fact_a"] for r in rows} | {r["fact_b"] for r in rows}
    placeholders = ",".join("?" * len(wanted))
    facts = {
        row["id"]: _decode(row)
        for row in query(
            f"SELECT {_FACT_COLUMNS} FROM facts f JOIN documents d ON d.id = f.doc_id "
            f"WHERE f.id IN ({placeholders})",
            tuple(wanted),
        )
    }

    out = []
    for row in rows:
        a, b = facts.get(row["fact_a"]), facts.get(row["fact_b"])
        if not a or not b:
            continue
        out.append(
            {
                "id": row["id"],
                "relation": row["relation"],
                "dimension": row["dimension"],
                "reasoning": row["reasoning"],
                "reconciliation": row["reconciliation"],
                "confidence": row["confidence"],
                "similarity": row["similarity"],
                "cross_doc": bool(row["cross_doc"]),
                "decided_by": row["decided_by"],
                "created_at": row.get("created_at"),
                "a": a,
                "b": b,
            }
        )
    return out


def _relations(relation: str, *, cross_doc_only: bool, order: str, limit: int) -> list[dict[str, Any]]:
    rows = query(
        f"""SELECT * FROM relations
            WHERE relation = ? {'AND cross_doc = 1' if cross_doc_only else ''}
            ORDER BY {order}
            LIMIT ?""",
        (relation, limit),
    )
    return hydrate_relations(rows)


def case_corroboration(limit: int = 5) -> list[dict[str, Any]]:
    """Prefer corroborations where the two documents *phrased it differently*.

    An identical string appearing in two documents is a weak demonstration; the
    interesting case is 8,142 crore vs INR 81.4 billion, so pairs whose printed
    values or units differ are ranked first.
    """
    candidates = _relations(
        "corroborates",
        cross_doc_only=True,
        order="confidence DESC, similarity DESC",
        limit=limit * 6,
    )
    def score(rel: dict[str, Any]) -> tuple:
        a, b = rel["a"], rel["b"]
        printed_differs = (a["value_raw"] or "").strip().lower() != (b["value_raw"] or "").strip().lower()
        unit_differs = (a["unit_raw"] or "") != (b["unit_raw"] or "")
        wording_differs = (a["predicate"] or "").lower() != (b["predicate"] or "").lower()
        both_verified = a["verification"] != "unverified" and b["verification"] != "unverified"
        return (
            printed_differs or unit_differs,
            unit_differs,
            wording_differs,
            both_verified,
            rel["decided_by"] != "llm-flagged",
            rel["confidence"],
        )

    return sorted(candidates, key=score, reverse=True)[:limit]


def case_contradiction(limit: int = 5) -> list[dict[str, Any]]:
    """Genuine conflicts. Rank by the model's confidence, then by how far apart
    the numbers actually are - a 40% gap is a better demonstration than a 6% one."""
    candidates = _relations(
        "contradicts",
        cross_doc_only=False,
        order="confidence DESC",
        limit=limit * 6,
    )

    def gap(rel: dict[str, Any]) -> float:
        a, b = rel["a"].get("value_num"), rel["b"].get("value_num")
        if a is None or b is None:
            return 0.0
        biggest = max(abs(a), abs(b))
        return abs(a - b) / biggest if biggest else 0.0

    # Cross-document conflicts come first: two publishers disagreeing is the
    # point of the exercise, whereas a single document disagreeing with itself is
    # usually an extraction artefact from a table.
    return sorted(
        candidates,
        key=lambda r: (r["cross_doc"], r["decided_by"] != "llm-flagged", r["confidence"], gap(r)),
        reverse=True,
    )[:limit]


def case_reconcilable(limit: int = 5) -> list[dict[str, Any]]:
    """Apparent conflicts that context resolves. Require a named dimension and a
    written reconciliation - that is what makes the case interesting."""
    candidates = _relations(
        "reconcilable_context",
        cross_doc_only=False,
        order="confidence DESC",
        limit=limit * 6,
    )
    scored = [
        r
        for r in candidates
        if r["dimension"] and r["dimension"] != "none" and (r["reconciliation"] or "").strip()
    ] or candidates

    # Spread the examples across dimensions so the demo shows time, unit and
    # scope reconciliation rather than three variations of the same one, and
    # prefer cross-document pairs within each dimension.
    scored = sorted(
        scored,
        key=lambda r: (r["cross_doc"], r["decided_by"] != "llm-flagged", r["confidence"]),
        reverse=True,
    )
    by_dimension: dict[str, list[dict[str, Any]]] = {}
    for rel in scored:
        by_dimension.setdefault(rel["dimension"], []).append(rel)
    out: list[dict[str, Any]] = []
    while len(out) < limit and any(by_dimension.values()):
        for dim in list(by_dimension):
            if by_dimension[dim] and len(out) < limit:
                out.append(by_dimension[dim].pop(0))
    return out


def case_failures(limit: int = 8) -> dict[str, Any]:
    """The failure case, surfaced rather than hidden.

    Returns both the counted failure modes and concrete examples, so the UI can
    show what the system got wrong and what it did about it.
    """
    counts = query(
        "SELECT kind, severity, COUNT(*) AS n FROM issues GROUP BY kind, severity ORDER BY n DESC"
    )
    # Sample per kind rather than taking the top N overall: ordering by severity
    # alone returns eight instances of the single most common failure, which
    # shows one failure mode instead of the range of them.
    _KIND_ORDER = [
        "quote_not_found",
        "judgment_inconsistent",
        "chunk_failed",
        "judge_failed",
        "incomplete_fact",
        "unexpected_shape",
        "no_text_layer",
        "profile_failed",
        "pairs_over_budget",
        "quote_relocated",
    ]
    per_kind = max(1, limit // 4)
    kinds_present = [row["kind"] for row in counts]
    ordered_kinds = [k for k in _KIND_ORDER if k in kinds_present]
    ordered_kinds += [k for k in kinds_present if k not in ordered_kinds]

    examples: list[dict[str, Any]] = []
    for kind in ordered_kinds:
        examples.extend(
            query(
                """SELECT i.id, i.kind, i.severity, i.detail, i.payload, i.created_at,
                          d.filename, d.title AS doc_title
                   FROM issues i LEFT JOIN documents d ON d.id = i.doc_id
                   WHERE i.kind = ?
                   ORDER BY i.created_at DESC LIMIT ?""",
                (kind, per_kind),
            )
        )

    for row in examples:
        if row.get("payload"):
            try:
                row["payload"] = json.loads(row["payload"])
            except json.JSONDecodeError:
                pass

    totals = query("SELECT COUNT(*) AS n FROM facts")[0]["n"]
    rejected = query("SELECT COUNT(*) AS n FROM issues WHERE kind = 'quote_not_found'")[0]["n"]
    relocated = query("SELECT COUNT(*) AS n FROM facts WHERE verification = 'relocated'")[0]["n"]
    flagged = query("SELECT COUNT(*) AS n FROM relations WHERE decided_by = 'llm-flagged'")[0]["n"]

    return {
        "counts": counts,
        "examples": examples,
        "summary": {
            "facts_stored": totals,
            "facts_rejected_unverifiable_quote": rejected,
            "citations_auto_corrected": relocated,
            "flagged_judgments": flagged,
            "rejection_rate": round(rejected / (rejected + totals), 4) if (rejected + totals) else 0.0,
        },
    }


def build_showcase() -> dict[str, Any]:
    return {
        "corroboration": case_corroboration(),
        "contradiction": case_contradiction(),
        "reconcilable": case_reconcilable(),
        "failures": case_failures(),
    }
