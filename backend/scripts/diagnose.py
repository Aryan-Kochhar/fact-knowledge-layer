"""Corpus-level quality report. Read this after an ingest to see what went wrong.

Usage:  python scripts/diagnose.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import query  # noqa: E402


def section(title: str) -> None:
    print(f"\n{'=' * 88}\n{title}\n{'=' * 88}")


def main() -> None:
    section("documents")
    for row in query(
        """SELECT d.filename, d.page_count, d.status,
                  (SELECT COUNT(*) FROM facts f WHERE f.doc_id = d.id) fc,
                  (SELECT COUNT(*) FROM issues i WHERE i.doc_id = d.id) ic
           FROM documents d ORDER BY d.created_at"""
    ):
        print(f"  {row['filename'][:52]:<52} {row['page_count']:>4}p {row['fc']:>5} facts {row['ic']:>5} issues  {row['status']}")

    section("issues by kind")
    for row in query("SELECT kind, severity, COUNT(*) n FROM issues GROUP BY kind, severity ORDER BY n DESC"):
        print(f"  {row['n']:>5}  {row['severity']:<8} {row['kind']}")

    section("relations by type")
    for row in query(
        """SELECT relation, cross_doc, COUNT(*) n, ROUND(AVG(confidence), 2) conf
           FROM relations GROUP BY relation, cross_doc ORDER BY n DESC"""
    ):
        scope = "cross-doc" if row["cross_doc"] else "same-doc "
        print(f"  {row['n']:>5}  {scope}  {row['relation']:<24} avg conf {row['conf']}")

    section("relation dimensions (non-unrelated)")
    for row in query(
        """SELECT relation, dimension, COUNT(*) n FROM relations
           WHERE relation != 'unrelated' GROUP BY relation, dimension ORDER BY n DESC"""
    ):
        print(f"  {row['n']:>5}  {row['relation']:<24} {row['dimension']}")

    section("verification")
    for row in query("SELECT verification, COUNT(*) n, ROUND(AVG(verify_score),3) s FROM facts GROUP BY verification"):
        print(f"  {row['n']:>5}  {row['verification']:<14} avg score {row['s']}")

    section("normalisation coverage")
    total = query("SELECT COUNT(*) n FROM facts")[0]["n"]
    for label, sql in [
        ("numeric value parsed", "value_num IS NOT NULL"),
        ("period normalised", "period_key IS NOT NULL"),
        ("has qualifiers", "qualifiers != '[]'"),
        ("basis known", "period_basis != 'unknown'"),
        ("has printed page label", "printed_page IS NOT NULL"),
    ]:
        n = query(f"SELECT COUNT(*) n FROM facts WHERE {sql}")[0]["n"]
        pct = (n / total * 100) if total else 0
        print(f"  {n:>5}/{total}  {pct:5.1f}%  {label}")

    section("top periods")
    for row in query(
        "SELECT period_key, COUNT(*) n FROM facts WHERE period_key IS NOT NULL GROUP BY period_key ORDER BY n DESC LIMIT 12"
    ):
        print(f"  {row['n']:>5}  {row['period_key']}")

    section("LLM calls")
    for row in query(
        """SELECT purpose, model, status, COUNT(*) n, ROUND(AVG(latency_ms)) ms, ROUND(AVG(attempts),2) att
           FROM llm_calls GROUP BY purpose, model, status ORDER BY n DESC"""
    ):
        print(f"  {row['n']:>5}  {row['purpose']:<9} {row['model']:<24} {row['status']:<8} {row['ms']:>7}ms  avg attempts {row['att']}")

    section("sample rejected extractions (quote not found)")
    for row in query("SELECT detail, payload FROM issues WHERE kind='quote_not_found' ORDER BY RANDOM() LIMIT 5"):
        print(f"  · {row['detail'][:150]}")


if __name__ == "__main__":
    main()
