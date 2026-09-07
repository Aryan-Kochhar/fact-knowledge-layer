"""Evidence verification.

This is the guard that turns "the model said so" into "the document says so".

Every extracted fact claims a verbatim quote and a page. Before we store it we
check that the quote actually occurs in the text we sent, on the page claimed.
Three outcomes:

  verified   - found on the claimed page (exactly, or modulo whitespace and
               punctuation, which PDF text extraction mangles routinely)
  relocated  - found, but on a different page in the same chunk; the citation is
               corrected and the correction is logged
  unverified - not found anywhere in the chunk. The fact is rejected and written
               to the issues table so the failure is visible rather than silent.

Matching is deliberately tolerant of whitespace and punctuation and intolerant
of everything else: a model that paraphrases, merges two sentences, or invents a
number will not clear the threshold.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

_WS_RE = re.compile(r"\s+")
# Characters PDF extraction routinely mangles or drops.
_SOFT_PUNCT = "‘’“”–—−`'\"()[]{}.,;:!?%*|/\\-–—_"


@dataclass
class QuoteMatch:
    score: float
    start: int | None = None
    end: int | None = None
    method: str = "none"

    @property
    def found(self) -> bool:
        return self.start is not None


def _normalise_with_map(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace, lowercase, and keep a map back to original offsets."""
    out: list[str] = []
    index: list[int] = []
    prev_space = True
    for i, ch in enumerate(text):
        if ch.isspace():
            if not prev_space:
                out.append(" ")
                index.append(i)
                prev_space = True
            continue
        out.append(ch.lower())
        index.append(i)
        prev_space = False
    return "".join(out), index


def _strip_soft(text: str) -> str:
    return "".join(" " if ch in _SOFT_PUNCT else ch for ch in text)


def match_quote(quote: str, page_text: str, threshold: float) -> QuoteMatch:
    """Locate `quote` inside `page_text`, tolerating extraction noise."""
    if not quote or not quote.strip() or not page_text:
        return QuoteMatch(score=0.0)

    norm_page, index = _normalise_with_map(page_text)
    norm_quote = _WS_RE.sub(" ", quote.strip().lower())
    if not norm_quote:
        return QuoteMatch(score=0.0)

    def to_original(a: int, b: int) -> tuple[int, int]:
        a = max(0, min(a, len(index) - 1))
        b = max(0, min(b, len(index) - 1))
        return index[a], index[b] + 1

    # 1. exact (after whitespace collapse)
    pos = norm_page.find(norm_quote)
    if pos != -1:
        start, end = to_original(pos, pos + len(norm_quote) - 1)
        return QuoteMatch(score=1.0, start=start, end=end, method="exact")

    # 2. punctuation-insensitive
    soft_page = _strip_soft(norm_page)
    soft_quote = _WS_RE.sub(" ", _strip_soft(norm_quote)).strip()
    if soft_quote:
        pos = soft_page.find(soft_quote)
        if pos != -1:
            start, end = to_original(pos, pos + len(soft_quote) - 1)
            return QuoteMatch(score=0.97, start=start, end=end, method="punctuation_normalised")

        # 2b. whitespace-insensitive: PDFs split words across line breaks and
        # sometimes drop the space entirely.
        tight_page = soft_page.replace(" ", "")
        tight_quote = soft_quote.replace(" ", "")
        if tight_quote:
            tpos = tight_page.find(tight_quote)
            if tpos != -1:
                # Map the space-free offset back by counting non-space chars.
                seen = 0
                start_norm = end_norm = None
                for i, ch in enumerate(soft_page):
                    if ch != " ":
                        if seen == tpos and start_norm is None:
                            start_norm = i
                        if seen == tpos + len(tight_quote) - 1:
                            end_norm = i
                            break
                        seen += 1
                if start_norm is not None and end_norm is not None:
                    start, end = to_original(start_norm, end_norm)
                    return QuoteMatch(score=0.93, start=start, end=end, method="whitespace_insensitive")

    # 3. fuzzy window around the longest common block
    matcher = SequenceMatcher(None, soft_page, soft_quote, autojunk=False)
    block = matcher.find_longest_match(0, len(soft_page), 0, len(soft_quote))
    if block.size < max(12, len(soft_quote) * 0.25):
        return QuoteMatch(score=0.0)

    win_start = max(0, block.a - block.b)
    win_end = min(len(soft_page), win_start + len(soft_quote))
    window = soft_page[win_start:win_end]
    score = SequenceMatcher(None, window, soft_quote, autojunk=False).ratio()
    if score >= threshold:
        start, end = to_original(win_start, max(win_start, win_end - 1))
        return QuoteMatch(score=round(score, 3), start=start, end=end, method="fuzzy")

    return QuoteMatch(score=round(score, 3))


def locate_quote(
    quote: str,
    pages: dict[int, str],
    claimed_page: int | None,
    threshold: float,
) -> tuple[int | None, QuoteMatch]:
    """Find the page a quote really came from.

    Tries the claimed page first (the common case), then every other page in the
    chunk, keeping the best match. Returns (page_index, match).
    """
    if not pages:
        return None, QuoteMatch(score=0.0)

    if claimed_page in pages:
        hit = match_quote(quote, pages[claimed_page], threshold)
        if hit.found:
            return claimed_page, hit

    best_page: int | None = None
    best = QuoteMatch(score=0.0)
    for page_index, text in pages.items():
        if page_index == claimed_page:
            continue
        hit = match_quote(quote, text, threshold)
        if hit.score > best.score:
            best_page, best = page_index, hit
        if hit.score >= 0.999:
            break

    if best.found:
        return best_page, best
    return None, best
