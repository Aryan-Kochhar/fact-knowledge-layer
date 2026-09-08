"""Measure the pipeline against what is actually on the pages. No API calls.

Three questions, answered separately because they fail for different reasons:

  1. Is every stored fact anchored to evidence?      (integrity, whole corpus)
  2. Does a stored fact carry the number its own     (precision proxy, whole
     quote carries?                                   corpus)
  3. Does the pipeline find the facts a human         (recall, hand-labelled set)
     reading the page would have written down?

The first two run over all 3,460 stored facts and need no ground truth at all -
they are properties the system can be held to on any corpus, including PDFs it
has never seen. The third needs hand-labelled facts and so is necessarily small;
see eval/labeled_facts.json for how they were chosen.

Recall is reported alone rather than folded into an F1. An F1 here would divide
a recall over thirty labels by a precision over a filtered slice of thousands,
and the resulting number would look authoritative while meaning very little.

    python eval/run_eval.py            # report
    python eval/run_eval.py --verbose  # also list every match
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import query  # noqa: E402
from eval.matching import (  # noqa: E402
    digits,
    metric_matches,
    metric_overlap,
    normalisation_correct,
    period_correct,
    value_matches,
)

LABELS_PATH = Path(__file__).resolve().parent / "labeled_facts.json"

RULE = "─" * 74


def heading(text: str) -> None:
    print(f"\n{RULE}\n{text}\n{RULE}")


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.2f}%" if whole else "n/a"


# ---------------------------------------------------------------------------
# 1. Evidence integrity
# ---------------------------------------------------------------------------

def report_integrity(facts: list[dict]) -> None:
    heading("1. EVIDENCE INTEGRITY  (whole corpus, no ground truth needed)")

    verification = Counter(f["verification"] for f in facts)
    located = verification["verified"] + verification["relocated"]

    rejected = query(
        "SELECT COUNT(*) AS n FROM issues WHERE kind = 'quote_not_found'"
    )[0]["n"]
    proposed = len(facts) + rejected

    print(f"  facts stored                    {len(facts):>8,}")
    print(f"  quote located in the source     {located:>8,}   {pct(located, len(facts))}")
    print(f"    on the page the model cited   {verification['verified']:>8,}")
    print(f"    found on another page         {verification['relocated']:>8,}")
    print(f"  proposed but rejected           {rejected:>8,}   "
          f"{pct(rejected, proposed)} of everything proposed")

    if verification["unverified"]:
        print(f"\n  !! {verification['unverified']} stored facts are unverified. "
              f"The gate is supposed to make this impossible.")


# ---------------------------------------------------------------------------
# 2. Value-in-quote precision proxy
# ---------------------------------------------------------------------------

def report_value_in_quote(facts: list[dict], verbose: bool) -> None:
    heading("2. VALUE PRESENT IN ITS OWN QUOTE  (whole corpus)")
    print("  A quote that verified against the page still says nothing about")
    print("  whether the number attached to it came from that quote. This checks")
    print("  the join: does the extracted value appear in the evidence text?\n")

    hit, misses, skipped = 0, [], 0
    for f in facts:
        want = digits(f["value_raw"])
        if not want:
            skipped += 1
            continue
        if want in digits(f["quote"]) or (f["value_raw"] or "").strip() in (f["quote"] or ""):
            hit += 1
        else:
            misses.append(f)

    checked = hit + len(misses)
    print(f"  numeric facts checked           {checked:>8,}")
    print(f"  value found in its quote        {hit:>8,}   {pct(hit, checked)}")
    print(f"  value absent from its quote     {len(misses):>8,}   {pct(len(misses), checked)}")
    print(f"  non-numeric, not applicable     {skipped:>8,}")

    if misses:
        print("\n  Every absence, with its quote - these are read manually rather")
        print("  than assumed wrong, because most are the model resolving a number")
        print("  the sentence states in words or shows in a chart:\n")
        for f in misses if verbose else misses[:12]:
            quote = (f["quote"] or "").replace("\n", " ")[:78]
            print(f"    {f['value_raw']!r:<20} {f['predicate'][:34]:<34}")
            print(f"      {quote!r}")
        if not verbose and len(misses) > 12:
            print(f"    ... {len(misses) - 12} more (--verbose)")


# ---------------------------------------------------------------------------
# 3. Recall against hand-labelled facts
# ---------------------------------------------------------------------------

def report_recall(facts: list[dict], verbose: bool) -> None:
    payload = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
    labels = payload["facts"]

    heading(f"3. RECALL AGAINST {len(labels)} HAND-LABELLED FACTS")
    print("  Labelled by reading the source pages, before looking at what the")
    print("  pipeline produced. A label counts as found only when some stored")
    print("  fact names the same metric in the same document AND carries the")
    print("  same number.\n")

    by_doc: dict[str, list[dict]] = {}
    for f in facts:
        by_doc.setdefault(f["filename"], []).append(f)

    found, offpage, renamed, missed = [], [], [], []
    period_hits = period_checked = 0
    norm_hits = norm_checked = 0
    by_difficulty: Counter = Counter()
    difficulty_total: Counter = Counter()

    for label in labels:
        difficulty_total[label.get("difficulty", "?")] += 1
        candidates = by_doc.get(label["document"], [])

        named = [
            f for f in candidates
            if metric_matches(label["metric"], f["subject"], f["predicate"], f["qualifiers"])
        ]
        agreeing = [f for f in named if value_matches(label, f["value_raw"], f["value_num"])]

        # A label points at the page a human read it off. Requiring the match to
        # sit on that page is what stops a numerically similar fact from
        # elsewhere in the document being counted as a find - an earlier version
        # of this harness let an exchange rate quoted in a different quarter
        # satisfy a label, and scored itself higher for it.
        on_page = [f for f in agreeing if f["page_index"] == label["page_index"]]
        matched = on_page[0] if on_page else None

        if matched is None:
            if agreeing:
                # Right metric, right number, different page: the knowledge layer
                # does hold this fact, sourced from somewhere else in the same
                # document. Not a find, but not a blank either.
                offpage.append((label, agreeing[0]))
            else:
                # Last resort: does any fact in this document carry the number,
                # under a name the metric rule did not recognise? It must still
                # share at least one content word, or a coincidence of digits
                # counts as a find - "personal loans growth" and "net outward
                # foreign direct investment" are both 14.0 in the RBI report.
                elsewhere = next(
                    (f for f in candidates
                     if value_matches(label, f["value_raw"], f["value_num"])
                     and metric_overlap(
                         label["metric"], f["subject"], f["predicate"], f["qualifiers"]
                     ) > 0),
                    None,
                )
                if elsewhere is not None:
                    renamed.append((label, elsewhere))
                else:
                    missed.append((label, named))
            continue

        found.append((label, matched))
        by_difficulty[label.get("difficulty", "?")] += 1

        ok = period_correct(label, matched["period_key"])
        if ok is not None:
            period_checked += 1
            period_hits += ok

        ok = normalisation_correct(label, matched["value_num"])
        if ok is not None:
            norm_checked += 1
            norm_hits += ok

    print(f"  recall, on the labelled page    {len(found):>8}/{len(labels)}   "
          f"{pct(len(found), len(labels))}")
    print(f"  same fact, elsewhere in the doc {len(offpage):>8}/{len(labels)}   "
          f"{pct(len(offpage), len(labels))}")
    print(f"  number present, metric renamed  {len(renamed):>8}/{len(labels)}   "
          f"{pct(len(renamed), len(labels))}")
    print(f"  not in the knowledge layer      {len(missed):>8}/{len(labels)}   "
          f"{pct(len(missed), len(labels))}")
    covered = len(found) + len(offpage) + len(renamed)
    print(f"  {'':<31} {'':>8}          "
          f"{pct(covered, len(labels))} of labels are represented somehow")
    print()
    print(f"  period key correct              {period_hits:>8}/{period_checked}   "
          f"{pct(period_hits, period_checked)}   (of those found)")
    print(f"  normalised value correct        {norm_hits:>8}/{norm_checked}   "
          f"{pct(norm_hits, norm_checked)}   (of those found)")

    print("\n  by difficulty, as labelled up front:")
    for level in ("easy", "medium", "hard"):
        total = difficulty_total[level]
        if total:
            print(f"    {level:<8} {by_difficulty[level]:>3}/{total:<3} {pct(by_difficulty[level], total)}")

    if offpage:
        print(f"\n  FOUND, BUT SOURCED FROM ANOTHER PAGE ({len(offpage)}):")
        for label, f in offpage:
            print(f"    {label['id']}  {label['metric']} = {label['value_text']}")
            print(f"          labelled p{label['page_index']}, extracted from p{f['page_index']} "
                  f"as {f['predicate'][:44]!r}")

    if renamed:
        print(f"\n  NUMBER FOUND UNDER A NAME THE RULE DID NOT MATCH ({len(renamed)}):")
        for label, f in renamed:
            print(f"    {label['id']}  labelled {label['metric']!r}")
            print(f"          stored as {f['predicate'][:52]!r} "
                  f"= {f['value_raw']} [{f['period_key']}] p{f['page_index']}")

    if missed:
        print(f"\n  MISSED ({len(missed)}):")
        for label, named in missed:
            print(f"    {label['id']}  {label['metric']} = {label['value_text']} "
                  f"{label['unit']} [{label.get('difficulty')}]")
            print(f"          {label['document']} p{label['page_index']}")
            if named:
                near = named[0]
                print(f"          metric was found but the value differed: "
                      f"{near['value_raw']} {near['unit_raw'] or ''} [{near['period_key']}]")
            else:
                print("          no stored fact names this metric in this document")
            if label.get("note"):
                print(f"          note: {label['note']}")

    wrong_period = [
        (lbl, f) for lbl, f in found
        if period_correct(lbl, f["period_key"]) is False
    ]
    if wrong_period:
        print(f"\n  FOUND BUT MIS-DATED ({len(wrong_period)}):")
        for lbl, f in wrong_period:
            print(f"    {lbl['id']}  {lbl['metric']}: expected {lbl['period']}, "
                  f"got {f['period_key']}  (raw: {f['time_scope_raw']!r})")

    if verbose:
        print("\n  FOUND:")
        for lbl, f in found:
            print(f"    {lbl['id']}  {lbl['metric']} = {f['value_raw']} {f['unit_raw'] or ''} "
                  f"[{f['period_key']}]  p{f['page_index']}")


def main(verbose: bool) -> int:
    settings.seed_if_empty()

    facts = query(
        "SELECT f.*, d.filename FROM facts f JOIN documents d ON d.id = f.doc_id"
    )
    if not facts:
        print("no facts stored - ingest something first, or restore samples/facts.db")
        return 1

    docs = query("SELECT COUNT(*) AS n, SUM(page_count) AS pages FROM documents")[0]
    print(f"\ncorpus: {docs['n']} documents, {docs['pages']} pages, {len(facts):,} facts")
    print(f"database: {settings.db_path}")

    report_integrity(facts)
    report_value_in_quote(facts, verbose)
    report_recall(facts, verbose)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--verbose" in sys.argv or "-v" in sys.argv))
