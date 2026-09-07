"""Page-aware chunking.

Two constraints pull against each other:

* Big chunks mean fewer LLM calls (free-tier quota is the binding resource) and
  give the model enough surrounding context to resolve what a table column
  means.
* Small chunks keep the model's attention on the whole span, make a failed call
  cheap to retry, and keep page attribution tight.

The compromise: accumulate whole pages up to a character target, never split a
page across two chunks unless the page alone exceeds the hard cap, and stamp
every page's text with a `[[page N]]` marker so the model can attribute each
quote. N is the PDF page index, which is the primary key we store against; the
printed label is attached at display time.

Nothing here is document-specific: it is driven purely by character counts and
paragraph boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import settings
from .pdf_parse import PageContent

# Pages with less than this are structural (dividers, blank backs) - they get
# folded into a neighbour rather than costing a call of their own.
_TRIVIAL_PAGE_CHARS = 30


@dataclass
class Chunk:
    ordinal: int
    page_start: int
    page_end: int
    text: str

    @property
    def char_count(self) -> int:
        return len(self.text)


def _marker(page: PageContent) -> str:
    return f"[[page {page.page_index}]]"


def _split_long_page(page: PageContent, limit: int) -> list[str]:
    """Split one oversized page on paragraph boundaries, then on lines."""
    blocks = re.split(r"\n\s*\n", page.text)
    parts: list[str] = []
    current: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        if current:
            parts.append("\n\n".join(current))
            current = []
            size = 0

    for block in blocks:
        if len(block) > limit:
            # A single paragraph (usually a wide table) larger than the cap.
            flush()
            lines = block.splitlines()
            buf: list[str] = []
            buf_size = 0
            for line in lines:
                if buf and buf_size + len(line) > limit:
                    parts.append("\n".join(buf))
                    buf, buf_size = [], 0
                buf.append(line)
                buf_size += len(line) + 1
            if buf:
                parts.append("\n".join(buf))
            continue
        if size + len(block) > limit and current:
            flush()
        current.append(block)
        size += len(block) + 2
    flush()
    return [p for p in parts if p.strip()]


def build_chunks(pages: list[PageContent]) -> list[Chunk]:
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_pages: list[int] = []
    buf_size = 0

    def flush() -> None:
        nonlocal buf, buf_pages, buf_size
        if not buf:
            return
        chunks.append(
            Chunk(
                ordinal=len(chunks),
                page_start=min(buf_pages),
                page_end=max(buf_pages),
                text="\n\n".join(buf),
            )
        )
        buf, buf_pages, buf_size = [], [], 0

    for page in pages:
        text = page.text.strip()
        if len(text) < _TRIVIAL_PAGE_CHARS:
            continue

        if len(text) > settings.chunk_max_chars:
            # Oversized page: emit it as its own run of chunks so its parts are
            # never diluted by neighbouring pages.
            flush()
            for part in _split_long_page(page, settings.chunk_max_chars):
                chunks.append(
                    Chunk(
                        ordinal=len(chunks),
                        page_start=page.page_index,
                        page_end=page.page_index,
                        text=f"{_marker(page)}\n{part}",
                    )
                )
            continue

        piece = f"{_marker(page)}\n{text}"
        if buf and buf_size + len(piece) > settings.chunk_target_chars:
            flush()
        buf.append(piece)
        buf_pages.append(page.page_index)
        buf_size += len(piece) + 2

    flush()

    # Merge a trailing runt into its predecessor so we never spend a whole call
    # on 200 characters.
    if len(chunks) >= 2 and chunks[-1].char_count < settings.page_min_chars:
        tail = chunks.pop()
        prev = chunks[-1]
        chunks[-1] = Chunk(
            ordinal=prev.ordinal,
            page_start=prev.page_start,
            page_end=tail.page_end,
            text=prev.text + "\n\n" + tail.text,
        )

    for i, chunk in enumerate(chunks):
        chunk.ordinal = i
    return chunks
