"""Print a readable sample of what was extracted. Quality-check aid.

Usage:
    python scripts/peek.py facts [n]
    python scripts/peek.py relations [type]
    python scripts/peek.py issues
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import query  # noqa: E402


def show_facts(limit: int = 20) -> None:
    rows = query(
        """SELECT f.*, d.filename FROM facts f JOIN documents d ON d.id = f.doc_id
           ORDER BY f.confidence DESC, f.page_index LIMIT ?""",
        (limit,),
    )
    for row in rows:
        print("─" * 100)
        print(f"{row['subject']}  |  {row['predicate']}")
        print(f"  value      : {row['value_raw']!r}  unit={row['unit_raw']!r}  time={row['time_scope_raw']!r}")
        print(
            f"  normalised : num={row['value_num']} unit={row['value_unit']} dim={row['value_dim']} "
            f"period={row['period_key']} basis={row['period_basis']}"
        )
        print(f"  qualifiers : {row['qualifiers']}")
        print(
            f"  evidence   : pdf p{row['page_index']} (printed {row['printed_page']}) "
            f"{row['verification']} score={row['verify_score']}"
        )
        print(f"  quote      : {row['quote'][:180]!r}")


def show_relations(kind: str | None = None) -> None:
    where = "WHERE r.relation = ?" if kind else "WHERE r.relation != 'unrelated'"
    params = (kind,) if kind else ()
    rows = query(
        f"""SELECT r.*,
                   fa.subject AS a_subj, fa.predicate AS a_pred, fa.value_raw AS a_val,
                   fa.unit_raw AS a_unit, fa.time_scope_raw AS a_time, fa.page_index AS a_page,
                   fa.printed_page AS a_printed, da.filename AS a_doc, fa.quote AS a_quote,
                   fb.subject AS b_subj, fb.predicate AS b_pred, fb.value_raw AS b_val,
                   fb.unit_raw AS b_unit, fb.time_scope_raw AS b_time, fb.page_index AS b_page,
                   fb.printed_page AS b_printed, db.filename AS b_doc, fb.quote AS b_quote
            FROM relations r
            JOIN facts fa ON fa.id = r.fact_a JOIN documents da ON da.id = fa.doc_id
            JOIN facts fb ON fb.id = r.fact_b JOIN documents db ON db.id = fb.doc_id
            {where}
            ORDER BY r.confidence DESC LIMIT 12""",
        params,
    )
    for row in rows:
        print("═" * 100)
        print(
            f"{row['relation'].upper()}  [{row['dimension']}]  conf={row['confidence']:.2f} "
            f"sim={row['similarity']:.2f}  {'cross-doc' if row['cross_doc'] else 'same-doc'}"
        )
        print(f"  A  {row['a_subj']} | {row['a_pred']}")
        print(f"     = {row['a_val']} {row['a_unit'] or ''}  ({row['a_time']})   [{row['a_doc'][:34]} p{row['a_printed'] or row['a_page']}]")
        print(f"     “{row['a_quote'][:150]}”")
        print(f"  B  {row['b_subj']} | {row['b_pred']}")
        print(f"     = {row['b_val']} {row['b_unit'] or ''}  ({row['b_time']})   [{row['b_doc'][:34]} p{row['b_printed'] or row['b_page']}]")
        print(f"     “{row['b_quote'][:150]}”")
        print(f"  → {row['reasoning']}")
        if row["reconciliation"]:
            print(f"  ⇒ {row['reconciliation']}")


def show_issues() -> None:
    rows = query("SELECT i.*, d.filename FROM issues i LEFT JOIN documents d ON d.id = i.doc_id ORDER BY i.created_at DESC LIMIT 25")
    if not rows:
        print("no issues logged")
    for row in rows:
        print("─" * 100)
        print(f"[{row['severity']}] {row['kind']}  ({row['filename']})")
        print(f"  {row['detail']}")
        if row["payload"]:
            print(f"  payload: {str(row['payload'])[:400]}")


if __name__ == "__main__":
    args = sys.argv[1:]
    mode = args[0] if args else "facts"
    if mode == "facts":
        show_facts(int(args[1]) if len(args) > 1 else 20)
    elif mode == "relations":
        show_relations(args[1] if len(args) > 1 else None)
    elif mode == "issues":
        show_issues()
    else:
        print(__doc__)
