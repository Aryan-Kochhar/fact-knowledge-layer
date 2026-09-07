"""Show rejected extractions next to the source text, to diagnose why the quote missed.

Usage:  python scripts/debug_reject.py [n]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import query  # noqa: E402
from app.pipeline.verify import match_quote  # noqa: E402


def main(n: int = 5) -> None:
    rows = query(
        """SELECT i.payload, i.detail, i.chunk_id, i.doc_id, d.filename
           FROM issues i JOIN documents d ON d.id = i.doc_id
           WHERE i.kind = 'quote_not_found' ORDER BY RANDOM() LIMIT ?""",
        (n,),
    )
    for row in rows:
        payload = json.loads(row["payload"]) if row["payload"] else {}
        fact = payload.get("fact", {})
        quote = fact.get("quote", "")
        claimed = fact.get("page")

        chunk = query("SELECT page_start, page_end FROM chunks WHERE id = ?", (row["chunk_id"],))
        if not chunk:
            continue
        lo, hi = chunk[0]["page_start"], chunk[0]["page_end"]
        pages = query(
            "SELECT page_index, text FROM pages WHERE doc_id = ? AND page_index BETWEEN ? AND ?",
            (row["doc_id"], lo, hi),
        )

        print("=" * 100)
        print(f"{row['filename'][:60]}   chunk pages {lo}-{hi}, model cited page {claimed}")
        print(f"  subject   : {fact.get('subject')} | {fact.get('predicate')}")
        print(f"  value     : {fact.get('value')} {fact.get('unit')}  ({fact.get('time_scope')})")
        print(f"  QUOTE     : {quote!r}")

        # Which page has the most overlap, and what does that region look like?
        best_page, best = None, 0.0
        for page in pages:
            score = match_quote(quote, page["text"], 0.0).score
            if score > best:
                best_page, best = page, score
        print(f"  best page : {best_page['page_index'] if best_page else '-'} score={best:.2f}")

        # Look for the fact's number in the page text, which tells us whether the
        # data is present but worded differently, or absent entirely.
        value = str(fact.get("value") or "").strip()
        if value and best_page:
            hit = best_page["text"].find(value)
            if hit >= 0:
                lo_i, hi_i = max(0, hit - 220), min(len(best_page["text"]), hit + 220)
                print(f"  value {value!r} IS on page {best_page['page_index']}; surrounding text:")
                print("    " + best_page["text"][lo_i:hi_i].replace("\n", " ⏎ "))
            else:
                print(f"  value {value!r} NOT found anywhere on the best page")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 5)
