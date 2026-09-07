"""PDF -> page text.

PyMuPDF rather than pdfplumber for the main text pass: on the 100-page starter
filings it is roughly an order of magnitude faster and its reading-order sort
handles the two-column layouts in the RBI and IMF reports without shredding
sentences. pdfplumber stays available for the diagnostic endpoint where its
word-level boxes are more useful.

Two things here matter downstream:

* **Tables are rendered into the page text.** A large share of the comparable
  numbers in these documents only exist inside tables. Whatever we render here
  is exactly what we store and exactly what we send to the model, so a quote
  taken from a table row can still be verified character-for-character later.

* **Printed page labels are recovered.** The curated excerpts keep the original
  documents' printed page numbers, so PDF position 40 may be printed page 118.
  A citation is only useful if a human can find it in the real document, so we
  detect the printed label and validate it by looking for monotonic runs across
  neighbouring pages.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

from ..config import settings

log = logging.getLogger("fkl.pdf")


@dataclass
class PageContent:
    page_index: int          # 1-based position in the PDF file
    text: str
    printed_label: str | None = None
    label_candidates: list[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return len(self.text)


# --------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------

def _clean_block(text: str) -> str:
    """One text block becomes one continuous paragraph.

    PDF line breaks fall mid-sentence, so collapsing them inside a block turns
    the text back into prose. That matters directly for evidence: a model quotes
    a sentence, and a sentence that is stored broken across three lines with a
    neighbouring column wedged between them cannot be matched.
    """
    return re.sub(r"\s+", " ", text).strip()


def _column_aware_text(page: "fitz.Page") -> str:
    """Extract page text in true reading order, handling multi-column layouts.

    PyMuPDF's own `sort=True` orders by position, which on a two-column page
    interleaves the columns line by line and shreds every sentence. Institutional
    reports (the RBI Annual Report, IMF staff reports, the Economic Survey) are
    overwhelmingly two-column, so this is not an edge case for this problem
    domain - it decides whether the document is usable at all.

    Approach: classify each block as belonging to the left column, the right
    column, or spanning the full width. Full-width blocks (headings, wide tables)
    partition the page into horizontal bands; within each band the left column is
    emitted top-to-bottom, then the right. Pages that do not look two-column fall
    back to simple top-to-bottom, left-to-right ordering.
    """
    try:
        raw_blocks = page.get_text("blocks")
    except Exception:
        return page.get_text("text", sort=True) or ""

    blocks = [b for b in raw_blocks if len(b) > 6 and b[6] == 0 and str(b[4]).strip()]
    if not blocks:
        return ""

    rect = page.rect
    midpoint = (rect.x0 + rect.x1) / 2
    tolerance = rect.width * 0.04

    left, right, full = [], [], []
    for block in blocks:
        x0, x1 = block[0], block[2]
        if x1 <= midpoint + tolerance:
            left.append(block)
        elif x0 >= midpoint - tolerance:
            right.append(block)
        else:
            full.append(block)

    # Require real evidence of two columns before reordering anything: a few
    # blocks either side of the centre line is not enough, and a page that is
    # mostly full-width blocks is a single-column page with a stray narrow one.
    two_column = len(left) >= 3 and len(right) >= 3 and len(full) <= 0.4 * len(blocks)

    if not two_column:
        ordered = sorted(blocks, key=lambda b: (round(b[1] / 6), b[0]))
        return "\n".join(_clean_block(str(b[4])) for b in ordered)

    out: list[str] = []
    left.sort(key=lambda b: b[1])
    right.sort(key=lambda b: b[1])
    full.sort(key=lambda b: b[1])

    def drain(source: list, upto: float) -> list[str]:
        taken = []
        while source and source[0][1] < upto:
            taken.append(_clean_block(str(source.pop(0)[4])))
        return taken

    for spanning in full:
        boundary = spanning[1]
        out.extend(drain(left, boundary))
        out.extend(drain(right, boundary))
        out.append(_clean_block(str(spanning[4])))

    out.extend(drain(left, float("inf")))
    out.extend(drain(right, float("inf")))
    return "\n".join(part for part in out if part)


_SPACE_RUN_RE = re.compile(r"[ \t]{3,}")


def _collapse_whitespace(text: str) -> str:
    """Cap runs of spaces used purely as layout padding.

    Wide landscape pages (financial-statement notes, statements of changes in
    equity) come out of the text extractor as a handful of lines thousands of
    characters long, almost all of it padding used to position columns. One page
    in the starter set measured 145,352 characters of which 6,600 were content -
    it alone would have cost sixteen extraction calls. Capping the runs at three
    spaces keeps the visual column break, loses no digits (verified on the
    starter corpus), and cut that document's chunk count by roughly 70%.
    """
    text = _SPACE_RUN_RE.sub("   ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _clean_cell(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _render_table(rows: list[list[object]]) -> str:
    cleaned = [[_clean_cell(c) for c in row] for row in rows]
    cleaned = [r for r in cleaned if any(c for c in r)]
    if len(cleaned) < 2:
        return ""
    width = max(len(r) for r in cleaned)
    out = []
    for row in cleaned:
        padded = row + [""] * (width - len(row))
        out.append(" | ".join(padded))
    return "\n".join(out)


def _extract_tables(page: "fitz.Page") -> list[str]:
    if not settings.extract_tables:
        return []
    try:
        finder = page.find_tables()
    except Exception as exc:  # pragma: no cover - defensive, varies by PDF
        log.debug("table detection failed on page %s: %s", page.number, exc)
        return []

    rendered: list[str] = []
    seen: set[str] = set()
    for table in list(finder.tables)[: settings.max_tables_per_page]:
        try:
            rows = table.extract()
        except Exception:
            continue
        text = _render_table(rows)
        # Overlapping detections routinely yield the same grid twice; a duplicate
        # costs prompt budget and teaches the model nothing.
        if text and len(text) < 20_000 and text not in seen:
            seen.add(text)
            rendered.append(text)
    return rendered


# --------------------------------------------------------------------------
# printed page labels
# --------------------------------------------------------------------------

_ROMAN_RE = re.compile(r"^[ivxlcdm]{1,7}$", re.IGNORECASE)
_PAGE_WORD_RE = re.compile(r"(?:page|p\.)\s*([0-9]{1,4})", re.IGNORECASE)
_NUMBER_RE = re.compile(r"^[^0-9a-zA-Z]*([0-9]{1,4})[^0-9a-zA-Z]*$")


def _label_candidates(text: str) -> list[str]:
    """Pull plausible printed page numbers from a page's header and footer band."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    band = lines[-3:] + lines[:2]  # footers first: far more common in these docs
    found: list[str] = []
    for line in band:
        if len(line) > 90:
            continue
        m = _PAGE_WORD_RE.search(line)
        if m:
            found.append(m.group(1))
            continue
        m = _NUMBER_RE.match(line)
        if m:
            found.append(m.group(1))
            continue
        # "118 | Delhivery Limited" / "Annual Report 2024 | 118"
        for part in re.split(r"[|·•]", line):
            part = part.strip()
            if part.isdigit() and len(part) <= 4:
                found.append(part)
            elif _ROMAN_RE.match(part) and len(part) <= 5:
                found.append(part.lower())
    # preserve order, drop duplicates
    seen: set[str] = set()
    return [f for f in found if not (f in seen or seen.add(f))]


def _resolve_labels(pages: list[PageContent]) -> None:
    """Keep a candidate label only when neighbouring pages agree it is a sequence.

    A stray "2024" in a footer looks exactly like a page number in isolation.
    It stops looking like one as soon as the next page fails to say 2025.
    """
    numeric: list[list[int]] = []
    for page in pages:
        vals = []
        for c in page.label_candidates:
            if c.isdigit():
                vals.append(int(c))
        numeric.append(vals)

    chosen: list[int | None] = [None] * len(pages)
    for i in range(len(pages)):
        for value in numeric[i]:
            # Does some neighbour continue the run?
            forward = i + 1 < len(pages) and (value + 1) in numeric[i + 1]
            backward = i - 1 >= 0 and (value - 1) in numeric[i - 1]
            if forward or backward:
                chosen[i] = value
                break

    # Fill single-page gaps inside an otherwise consistent run.
    for i in range(1, len(pages) - 1):
        if chosen[i] is None and chosen[i - 1] is not None and chosen[i + 1] is not None:
            if chosen[i + 1] - chosen[i - 1] == 2:
                chosen[i] = chosen[i - 1] + 1

    for page, value in zip(pages, chosen):
        if value is not None:
            page.printed_label = str(value)
        elif page.label_candidates and not page.label_candidates[0].isdigit():
            page.printed_label = page.label_candidates[0]  # roman front matter


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def parse_pdf(path: Path) -> list[PageContent]:
    """Extract per-page text (including rendered tables) from a PDF."""
    pages: list[PageContent] = []
    with fitz.open(path) as doc:
        # Prefer the PDF's own page labels when the file declares them - they are
        # authoritative and free.
        declared: dict[int, str] = {}
        try:
            for i in range(doc.page_count):
                label = doc[i].get_label()
                if label:
                    declared[i + 1] = label
        except Exception:
            declared = {}

        for i, page in enumerate(doc, start=1):
            try:
                body = _column_aware_text(page)
            except Exception as exc:
                log.warning("text extraction failed on page %s of %s: %s", i, path.name, exc)
                try:
                    body = page.get_text("text", sort=True) or ""
                except Exception:
                    body = ""

            tables = _extract_tables(page)
            if tables:
                body = body.rstrip() + "\n\n" + "\n\n".join(
                    f"[table]\n{t}" for t in tables
                )

            body = _collapse_whitespace(body)

            content = PageContent(page_index=i, text=body)
            content.label_candidates = _label_candidates(body)
            if i in declared:
                content.printed_label = declared[i]
            pages.append(content)

    undeclared = [p for p in pages if p.printed_label is None]
    if undeclared:
        _resolve_labels(pages)
    return pages


def document_opening(pages: list[PageContent], max_chars: int = 9000) -> str:
    """Front matter used for the document-profile call.

    Skips near-empty cover pages so a title page with three words does not eat
    the whole budget, and stops at the character cap.
    """
    buf: list[str] = []
    used = 0
    for page in pages[:12]:
        if not page.text.strip():
            continue
        snippet = page.text[: max_chars - used]
        buf.append(f"[[page {page.printed_label or page.page_index}]]\n{snippet}")
        used += len(snippet)
        if used >= max_chars:
            break
    return "\n\n".join(buf)
