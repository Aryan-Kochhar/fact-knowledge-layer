"""Export a self-contained snapshot of results for reviewers with no API key.

The assignment asks that the project be evaluatable without the author's
account. Extraction and reconciliation need a Gemini key, so this writes out
both:

  samples/facts.db          the fully ingested corpus - copy it to data/ and the
                            whole UI works with no key and no API calls
  samples/showcase.json     the four required cases, evidence and reasoning
  samples/relations.json    every cross-document relationship found
  samples/facts.sample.json a readable slice of the fact store
  samples/issues.json       every rejection, repair and flagged verdict
  samples/stats.json        corpus-level counts

Usage:  python scripts/export_samples.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import PROJECT_DIR, settings  # noqa: E402
from app.db import query  # noqa: E402
from app.pipeline.showcase import build_showcase, hydrate_relations  # noqa: E402

OUT = PROJECT_DIR / "samples"


def write(name: str, payload: object) -> None:
    path = OUT / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    size = path.stat().st_size
    print(f"  {name:<26} {size / 1024:>8.1f} KB")


def main() -> int:
    if not settings.db_path.is_file():
        print("no database - ingest something first")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    print(f"writing to {OUT}\n")

    write("showcase.json", build_showcase())

    relations = hydrate_relations(
        query(
            """SELECT * FROM relations
               WHERE relation != 'unrelated'
               ORDER BY CASE relation WHEN 'contradicts' THEN 0
                                      WHEN 'reconcilable_context' THEN 1
                                      ELSE 2 END, confidence DESC"""
        )
    )
    write("relations.json", relations)

    facts = query(
        """SELECT f.*, d.filename, d.title AS doc_title
           FROM facts f JOIN documents d ON d.id = f.doc_id
           WHERE f.value_num IS NOT NULL
           ORDER BY f.confidence DESC LIMIT 400"""
    )
    for fact in facts:
        try:
            fact["qualifiers"] = json.loads(fact["qualifiers"])
        except (TypeError, json.JSONDecodeError):
            fact["qualifiers"] = []
    write("facts.sample.json", facts)

    issues = query("SELECT i.*, d.filename FROM issues i LEFT JOIN documents d ON d.id = i.doc_id")
    for issue in issues:
        if issue.get("payload"):
            try:
                issue["payload"] = json.loads(issue["payload"])
            except json.JSONDecodeError:
                pass
    write("issues.json", issues)

    write(
        "stats.json",
        {
            "documents": query(
                """SELECT filename, title, page_count,
                          (SELECT COUNT(*) FROM facts f WHERE f.doc_id = documents.id) AS facts
                   FROM documents ORDER BY created_at"""
            ),
            "facts": query("SELECT COUNT(*) AS n FROM facts")[0]["n"],
            "relations": {
                r["relation"]: r["n"]
                for r in query("SELECT relation, COUNT(*) AS n FROM relations GROUP BY relation")
            },
            "verification": {
                r["verification"]: r["n"]
                for r in query("SELECT verification, COUNT(*) AS n FROM facts GROUP BY verification")
            },
            "issues": {
                r["kind"]: r["n"]
                for r in query("SELECT kind, COUNT(*) AS n FROM issues GROUP BY kind")
            },
            "llm_calls": query(
                """SELECT purpose, model, COUNT(*) AS calls, ROUND(AVG(latency_ms)) AS avg_ms
                   FROM llm_calls GROUP BY purpose, model"""
            ),
        },
    )

    # Ship the database itself so a reviewer can open the real UI with no key.
    # WAL contents must be folded in first or the copy will be missing writes.
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy2(settings.db_path, OUT / "facts.db")
    print(f"  {'facts.db':<26} {(OUT / 'facts.db').stat().st_size / 1e6:>8.1f} MB")

    print("\nTo explore without an API key:")
    print("  cp samples/facts.db data/facts.db   then start the API and UI")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
