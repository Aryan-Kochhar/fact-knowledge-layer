"""Re-run the judgment consistency check over stored relations. No API calls.

The check in `linking.validate_judgment` compares a model's label against the
deterministic analysis of the same pair. Both of its inputs - the label and the
normalised facts - are already in the database, so the check can be replayed at
any time. That matters because the check is only as good as the normalisation
behind it: when a period-parsing bug is fixed, verdicts that were wrongly
flagged should be un-flagged, and verdicts that should have been flagged all
along should start being flagged.

Run `scripts/renormalize.py --apply` first so the facts carry current keys, then
this.

    python scripts/revalidate.py            # report
    python scripts/revalidate.py --apply    # write
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import execute_many, query  # noqa: E402
from app.pipeline.linking import analyse_pair, validate_judgment  # noqa: E402


def main(apply: bool) -> int:
    relations = query("SELECT * FROM relations")
    if not relations:
        print("no relations stored")
        return 1

    facts = {row["id"]: row for row in query("SELECT * FROM facts")}

    updates: list[tuple] = []
    moves = Counter()
    examples: dict[str, list[str]] = {}

    for rel in relations:
        a, b = facts.get(rel["fact_a"]), facts.get(rel["fact_b"])
        if not a or not b:
            continue

        # Recover the model's own number: older rows stored only the penalised
        # value, and the penalty was an exact halving.
        raw = rel["raw_confidence"]
        if raw is None:
            raw = min(1.0, rel["confidence"] * 2) if rel["decided_by"] == "llm-flagged" else rel["confidence"]

        problems = validate_judgment(rel["relation"], analyse_pair(a, b))
        decided_by = "llm-flagged" if problems else "llm"
        confidence = round(raw * 0.5, 3) if problems else round(raw, 3)

        was_flagged = rel["decided_by"] == "llm-flagged"
        now_flagged = bool(problems)
        if was_flagged != now_flagged:
            key = "un-flagged (was wrong to flag)" if was_flagged else "newly flagged"
            moves[key] += 1
            examples.setdefault(key, []).append(
                f"{a['subject']} — {a['predicate']} [{a['period_key']}] vs [{b['period_key']}] "
                f"({rel['relation']})"
            )

        if (
            decided_by != rel["decided_by"]
            or abs(confidence - rel["confidence"]) > 1e-6
            or rel["raw_confidence"] is None
        ):
            updates.append((confidence, raw, decided_by, rel["id"]))

    print(f"{len(relations)} relations examined, {len(updates)} rows would change\n")
    for label, count in moves.most_common():
        print(f"  {count:>5}  {label}")
        for line in examples.get(label, [])[:3]:
            print(f"           e.g. {line}")

    still = sum(1 for r in relations if r["decided_by"] == "llm-flagged") - moves["un-flagged (was wrong to flag)"]
    print(f"\n  flagged after revalidation: {still + moves['newly flagged']}")

    if not apply:
        print("\n(dry run - pass --apply to write)")
        return 0

    execute_many(
        "UPDATE relations SET confidence = ?, raw_confidence = ?, decided_by = ? WHERE id = ?",
        updates,
    )
    print(f"\napplied to {len(updates)} relations (0 API calls)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--apply" in sys.argv))
