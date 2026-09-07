"""Quick manual check that PDF parsing and page-label recovery behave.

Usage:  python scripts/smoke_parse.py <pdf> [<pdf> ...]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pipeline.pdf_parse import parse_pdf  # noqa: E402


def main(paths: list[str]) -> None:
    for raw in paths:
        path = Path(raw)
        t0 = time.perf_counter()
        pages = parse_pdf(path)
        elapsed = time.perf_counter() - t0

        chars = sum(p.char_count for p in pages)
        labelled = sum(1 for p in pages if p.printed_label)
        empty = sum(1 for p in pages if p.char_count < 50)

        print(f"\n=== {path.name} ===")
        print(f"pages={len(pages)}  chars={chars:,}  parse={elapsed:.1f}s")
        print(f"printed labels recovered: {labelled}/{len(pages)}   near-empty pages: {empty}")
        sample = [f"{p.page_index}->{p.printed_label}" for p in pages[:6]]
        tail = [f"{p.page_index}->{p.printed_label}" for p in pages[-4:]]
        print("label map (head):", ", ".join(sample))
        print("label map (tail):", ", ".join(tail))

        mid = pages[len(pages) // 2]
        print(f"--- page {mid.page_index} (printed {mid.printed_label}) first 600 chars ---")
        print(mid.text[:600].replace("\n", " ⏎ "))


if __name__ == "__main__":
    main(sys.argv[1:])
