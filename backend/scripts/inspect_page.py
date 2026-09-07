"""Inspect how a single page is being parsed. Debug aid for table extraction.

Usage:  python scripts/inspect_page.py <pdf> <page-index>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz  # noqa: E402

from app.pipeline.pdf_parse import _extract_tables, parse_pdf  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        print("usage: python scripts/inspect_page.py <pdf> [page-index]")
        return 1

    path = Path(argv[0])
    pages = parse_pdf(path)
    sizes = sorted(((p.char_count, p.page_index) for p in pages), reverse=True)
    print(f"{path.name}: {len(pages)} pages, {sum(s for s, _ in sizes):,} chars")
    print("largest pages:", ", ".join(f"p{idx}={size:,}" for size, idx in sizes[:8]))

    target = int(argv[1]) if len(argv) > 1 else sizes[0][1]
    with fitz.open(path) as doc:
        page = doc[target - 1]
        raw = page.get_text("text", sort=True) or ""
        tables = _extract_tables(page)

    print(f"\npage {target}: raw text {len(raw):,} chars, {len(tables)} tables "
          f"totalling {sum(len(t) for t in tables):,} chars")
    for i, table in enumerate(tables):
        rows = table.count("\n") + 1
        print(f"  table {i}: {rows} rows, {len(table):,} chars | first row: {table.splitlines()[0][:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
