"""Re-parse every ingested PDF and re-check stored quotes against the new text.

This is the regression test for changes to PDF extraction. Already-verified
quotes are ground truth that must keep matching; already-rejected quotes are the
population a fix is supposed to recover. Costs no API calls.

Usage:  python scripts/reverify.py [--apply]

    --apply  rewrite pages/facts in the database with the new parse, promoting
             any previously rejected fact whose quote now verifies.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import execute_many, query  # noqa: E402
from app.pipeline.pdf_parse import parse_pdf  # noqa: E402
from app.pipeline.verify import locate_quote  # noqa: E402

THRESH = settings.quote_match_threshold


def main(apply: bool) -> int:
    docs = query("SELECT id, filename, stored_path FROM documents ORDER BY created_at")
    if not docs:
        print("nothing ingested yet")
        return 1

    kept = lost = recovered = still_failing = 0
    page_rows: list[tuple] = []
    fact_updates: list[tuple] = []

    for doc in docs:
        path = Path(doc["stored_path"])
        if not path.is_file():
            print(f"  {doc['filename']}: stored file missing, skipping")
            continue

        pages = parse_pdf(path)
        page_text = {p.page_index: p.text for p in pages}
        labels = {p.page_index: p.printed_label for p in pages}

        # --- previously verified facts must still verify ---
        facts = query(
            "SELECT id, quote, page_index FROM facts WHERE doc_id = ? AND verification != 'unverified'",
            (doc["id"],),
        )
        doc_kept = doc_lost = 0
        for fact in facts:
            page, match = locate_quote(fact["quote"], page_text, fact["page_index"], THRESH)
            if match.found:
                doc_kept += 1
                fact_updates.append(
                    (
                        page,
                        labels.get(page),
                        "verified" if page == fact["page_index"] else "relocated",
                        match.score,
                        match.start,
                        match.end,
                        fact["id"],
                    )
                )
            else:
                doc_lost += 1

        # --- previously rejected quotes: do they verify now? ---
        issues = query(
            "SELECT payload, chunk_id FROM issues WHERE doc_id = ? AND kind = 'quote_not_found'",
            (doc["id"],),
        )
        doc_recovered = doc_still = 0
        for issue in issues:
            try:
                fact = (json.loads(issue["payload"]) or {}).get("fact", {})
            except (TypeError, json.JSONDecodeError):
                continue
            quote = fact.get("quote") or ""
            chunk = query("SELECT page_start, page_end FROM chunks WHERE id = ?", (issue["chunk_id"],))
            if not chunk:
                continue
            scoped = {
                i: t for i, t in page_text.items() if chunk[0]["page_start"] <= i <= chunk[0]["page_end"]
            }
            _, match = locate_quote(quote, scoped, fact.get("page"), THRESH)
            if match.found:
                doc_recovered += 1
            else:
                doc_still += 1

        kept += doc_kept
        lost += doc_lost
        recovered += doc_recovered
        still_failing += doc_still

        print(
            f"  {doc['filename'][:50]:<50} kept {doc_kept:>4}  lost {doc_lost:>4}  "
            f"recovered {doc_recovered:>4}  still failing {doc_still:>4}"
        )

        if apply:
            page_rows.extend(
                (doc["id"], p.page_index, p.printed_label, p.text, p.char_count) for p in pages
            )

    print(
        f"\nTOTAL  previously verified: {kept} still match, {lost} regressed"
        f"   |  previously rejected: {recovered} recovered, {still_failing} still failing"
    )

    if apply:
        execute_many(
            "INSERT OR REPLACE INTO pages (doc_id, page_index, printed_label, text, char_count) "
            "VALUES (?, ?, ?, ?, ?)",
            page_rows,
        )
        execute_many(
            """UPDATE facts SET page_index = ?, printed_page = ?, verification = ?,
                                verify_score = ?, quote_start = ?, quote_end = ? WHERE id = ?""",
            fact_updates,
        )
        print(f"\napplied: {len(page_rows)} pages rewritten, {len(fact_updates)} facts re-anchored")
        print("note: rejected facts are NOT resurrected here - re-ingest the document for that")

    return 0 if lost == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main("--apply" in sys.argv))
