"""Dry-run the parse + chunk stages and report the LLM call budget.

No API calls are made. Use this before ingesting a new corpus to check that the
free-tier quota will cover it.

Usage:  python scripts/plan_ingest.py <pdf-or-directory> [...]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.pipeline.chunking import build_chunks  # noqa: E402
from app.pipeline.pdf_parse import parse_pdf  # noqa: E402


def collect(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            out.extend(sorted(path.rglob("*.pdf")))
        elif path.suffix.lower() == ".pdf":
            out.append(path)
    return out


def main(argv: list[str]) -> int:
    pdfs = collect(argv)
    if not pdfs:
        print("usage: python scripts/plan_ingest.py <pdf-or-directory> [...]")
        return 1

    total_chunks = 0
    total_pages = 0
    print(f"{'document':<52} {'pages':>6} {'chars':>10} {'chunks':>7} {'calls':>6}")
    print("-" * 86)

    for path in pdfs:
        pages = parse_pdf(path)
        chunks = build_chunks(pages)
        chars = sum(p.char_count for p in pages)
        # one profile call + one call per chunk
        calls = 1 + len(chunks)
        total_chunks += len(chunks)
        total_pages += len(pages)
        print(f"{path.name[:52]:<52} {len(pages):>6} {chars:>10,} {len(chunks):>7} {calls:>6}")

    extraction_calls = total_chunks + len(pdfs)
    keys = max(len(settings.gemini_keys), 1)
    capacity = keys * settings.per_key_rpm

    print("-" * 86)
    print(f"{'TOTAL':<52} {total_pages:>6} {'':>10} {total_chunks:>7} {extraction_calls:>6}")
    print()
    print("Budget")
    print(f"  extraction calls           : {extraction_calls}")
    print("  judgment calls             : depends on cross-document overlap;")
    print(f"                               ~1 per {settings.judge_batch_size} candidate pairs")
    print(f"  key pool                   : {keys} key(s) x {settings.per_key_rpm} rpm = {capacity} rpm")
    print(f"  extraction wall clock (min): ~{extraction_calls / max(capacity, 1):.1f} at full rate")
    print()
    print("  Free-tier daily caps are per key. If a document is large, raise")
    print("  CHUNK_TARGET_CHARS to trade citation precision for fewer calls.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
