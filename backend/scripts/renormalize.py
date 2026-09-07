"""Re-derive every deterministic field from the stored raw extraction.

This exists because normalisation is deliberately separated from extraction. The
model's output - subject, predicate, printed value, printed unit, printed period,
qualifiers, quote - is stored verbatim. Everything used for comparison
(value_num, value_unit, period_key, period_basis, metric_key) is *derived* from
those raw fields by pure functions.

So when a parsing bug is found, the corpus does not have to be re-extracted:
this re-derives the comparison keys for every fact in a couple of seconds, for
zero API calls. Embeddings are only recomputed for facts whose claim text
actually changed.

    python scripts/renormalize.py            # report what would change
    python scripts/renormalize.py --apply    # write the changes

Note: relationships were judged against the old keys. After a change that moves
many periods or values, re-ingest to re-judge, or treat existing relations as
stale.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import execute_many, query  # noqa: E402
from app.pipeline.normalize import (  # noqa: E402
    canonical_claim,
    detect_basis,
    metric_key,
    normalize_period,
    parse_value,
)


def main(apply: bool) -> int:
    facts = query("SELECT * FROM facts")
    if not facts:
        print("no facts stored")
        return 1

    # Facts inherit their period from the document profile when their own text
    # states none, so re-derivation needs the same fallback the ingest used.
    fallbacks: dict[str, str | None] = {}
    for row in query("SELECT id, profile FROM documents"):
        try:
            profile = json.loads(row["profile"]) if row["profile"] else {}
        except json.JSONDecodeError:
            profile = {}
        fallbacks[row["id"]] = profile.get("reporting_period")

    updates: list[tuple] = []
    changed = Counter()
    examples: dict[str, list[str]] = {}

    for fact in facts:
        qualifiers = fact["qualifiers"]
        try:
            qualifiers = json.loads(qualifiers) if isinstance(qualifiers, str) else (qualifiers or [])
        except json.JSONDecodeError:
            qualifiers = []

        value = parse_value(fact["value_raw"], fact["unit_raw"])
        period = normalize_period(fact["time_scope_raw"], fallback=fallbacks.get(fact["doc_id"]))
        basis = detect_basis(fact["period_basis"], fact["quote"], fact["predicate"], fact["time_scope_raw"])
        key = metric_key(fact["subject"], fact["predicate"])
        claim = canonical_claim(fact["subject"], fact["predicate"], qualifiers)

        diffs = []
        if period.key != fact["period_key"]:
            diffs.append("period_key")
            examples.setdefault("period_key", []).append(
                f"{fact['time_scope_raw']!r}: {fact['period_key']} -> {period.key}"
            )
        if value.number != fact["value_num"]:
            diffs.append("value_num")
            examples.setdefault("value_num", []).append(
                f"{fact['value_raw']!r} {fact['unit_raw']!r}: {fact['value_num']} -> {value.number}"
            )
        if key != fact["metric_key"]:
            diffs.append("metric_key")
        if basis != fact["period_basis"]:
            diffs.append("period_basis")

        for name in diffs:
            changed[name] += 1

        if diffs:
            updates.append(
                (
                    value.number,
                    value.unit,
                    value.dimension,
                    period.key,
                    period.start,
                    period.end,
                    basis,
                    key,
                    fact["id"],
                )
            )
        _ = claim

    print(f"{len(facts)} facts examined, {len(updates)} would change\n")
    for field, count in changed.most_common():
        print(f"  {count:>5}  {field}")
        for line in examples.get(field, [])[:4]:
            print(f"           e.g. {line}")

    if not apply:
        print("\n(dry run - pass --apply to write)")
        return 0

    execute_many(
        """UPDATE facts SET value_num = ?, value_unit = ?, value_dim = ?,
                            period_key = ?, period_start = ?, period_end = ?,
                            period_basis = ?, metric_key = ? WHERE id = ?""",
        updates,
    )
    print(f"\napplied to {len(updates)} facts (0 API calls)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--apply" in sys.argv))
