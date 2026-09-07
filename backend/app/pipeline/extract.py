"""Chunk -> verified, normalised facts.

Order of operations matters here. The model proposes; verification and
normalisation dispose:

  LLM  ->  shape check  ->  quote verification  ->  numeric/period normalisation

A fact whose quote cannot be found in the source text never reaches the
database. That is the difference between a citation and a decoration.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..config import settings
from ..db import new_id, now_iso
from ..llm import prompts
from ..llm.gemini import generate_json
from ..security import scan_for_injection
from .normalize import (
    canonical_claim,
    detect_basis,
    display_claim,
    metric_key,
    normalize_period,
    parse_value,
)
from .verify import locate_quote

log = logging.getLogger("fkl.extract")

_ALLOWED_BASIS = {"actual", "estimate", "projection", "target", "unknown"}


class ExtractionResult:
    def __init__(self) -> None:
        self.facts: list[dict[str, Any]] = []
        self.issues: list[dict[str, Any]] = []

    def issue(self, kind: str, detail: str, payload: Any = None, severity: str = "warning") -> None:
        self.issues.append({"kind": kind, "detail": detail, "payload": payload, "severity": severity})


async def profile_document(filename: str, opening_text: str) -> dict[str, Any] | None:
    """One call per document to recover the context later pages leave implicit."""
    if not opening_text.strip():
        return None
    try:
        raw = await generate_json(
            prompts.profile_prompt(filename, opening_text),
            system=prompts.PROFILE_SYSTEM,
            purpose="profile",
            temperature=0.0,
            max_output_tokens=1024,
        )
    except Exception as exc:
        log.warning("profile call failed for %s: %s", filename, exc)
        return None

    if isinstance(raw, list) and raw:
        raw = raw[0]
    if not isinstance(raw, dict):
        return None
    return {k: v for k, v in raw.items() if v not in (None, "", "null")}


def _coerce_list(raw: Any) -> list[Any]:
    """Accept the shapes models actually return, not just the one we asked for."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("facts", "results", "items", "data", "output"):
            value = raw.get(key)
            if isinstance(value, list):
                return value
        # A single fact returned bare.
        if {"subject", "value"} <= set(raw.keys()):
            return [raw]
    return []


def _clean_qualifiers(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        text = str(item).strip()
        if text and text.lower() not in {"none", "n/a", "null"} and len(text) < 120:
            out.append(text)
    return out[:8]


def _as_float(value: Any, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(out, 0.0), 1.0)


async def extract_from_chunk(
    *,
    doc_id: str,
    chunk_id: str,
    chunk_text: str,
    page_texts: dict[int, str],
    printed_labels: dict[int, str | None],
    profile: dict[str, Any] | None,
) -> ExtractionResult:
    result = ExtractionResult()

    # A document that tries to talk to the model is worth recording. The prompt
    # fence stops it changing the task, and quote verification stops it inventing
    # evidence, but a reader should still be told the attempt was there - facts
    # from such a chunk deserve more scepticism, not less.
    injection = scan_for_injection(chunk_text)
    if injection:
        result.issue(
            "prompt_injection_suspected",
            (
                f"the source text on pages {min(page_texts)}–{max(page_texts)} contains "
                f"patterns typical of prompt injection ({', '.join(injection)}). The document "
                "block is fenced with a per-request token and its content was treated as data; "
                "facts from this chunk are flagged for review."
            ),
            payload={"patterns": injection},
            severity="warning",
        )

    raw = await generate_json(
        prompts.extract_prompt(chunk_text, profile, max_facts=settings.max_facts_per_chunk),
        system=prompts.EXTRACT_SYSTEM,
        purpose="extract",
        temperature=0.1,
        max_output_tokens=24576,
    )

    items = _coerce_list(raw)
    if not items:
        if raw not in ([], {}):
            result.issue(
                "unexpected_shape",
                f"model returned {type(raw).__name__} with no usable fact list",
                payload=str(raw)[:600],
            )
        return result

    fallback_period = (profile or {}).get("reporting_period")
    seen: set[tuple] = set()

    for item in items:
        if not isinstance(item, dict):
            result.issue("bad_item", "non-object element in fact array", payload=str(item)[:300])
            continue

        subject = str(item.get("subject") or "").strip()
        predicate = str(item.get("predicate") or "").strip()
        value_raw = item.get("value")
        value_raw = "" if value_raw is None else str(value_raw).strip()
        quote = str(item.get("quote") or "").strip()

        missing = [
            name
            for name, val in (("subject", subject), ("predicate", predicate), ("value", value_raw), ("quote", quote))
            if not val
        ]
        if missing:
            result.issue(
                "incomplete_fact",
                f"dropped a fact missing required field(s): {', '.join(missing)}",
                payload=item,
            )
            continue

        # --- evidence verification -------------------------------------------------
        claimed_page = item.get("page")
        try:
            claimed_page = int(claimed_page) if claimed_page is not None else None
        except (TypeError, ValueError):
            claimed_page = None

        page_index, match = locate_quote(quote, page_texts, claimed_page, settings.quote_match_threshold)

        if page_index is None:
            result.issue(
                "quote_not_found",
                (
                    f"rejected fact '{subject} — {predicate}': its quote does not appear in pages "
                    f"{min(page_texts)}–{max(page_texts)} (best similarity {match.score:.2f})."
                ),
                payload={"fact": item, "best_score": match.score},
            )
            continue

        if claimed_page is not None and page_index != claimed_page:
            result.issue(
                "quote_relocated",
                (
                    f"model cited page {claimed_page} but the quote for '{subject} — {predicate}' "
                    f"is on page {page_index}; citation corrected."
                ),
                payload={"claimed": claimed_page, "actual": page_index},
                severity="info",
            )
            verification = "relocated"
        else:
            verification = "verified"

        # --- normalisation ---------------------------------------------------------
        unit_raw = item.get("unit")
        unit_raw = None if unit_raw in (None, "", "null") else str(unit_raw).strip()
        time_raw = item.get("time_scope")
        time_raw = None if time_raw in (None, "", "null") else str(time_raw).strip()
        qualifiers = _clean_qualifiers(item.get("qualifiers"))

        parsed_value = parse_value(value_raw, unit_raw)
        period = normalize_period(time_raw, fallback=fallback_period)
        basis = detect_basis(item.get("basis"), quote, predicate, time_raw)
        if basis not in _ALLOWED_BASIS:
            basis = "unknown"

        key = metric_key(subject, predicate)
        dedupe_key = (key, period.key, parsed_value.number, value_raw.lower())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        result.facts.append(
            {
                "id": new_id("f_"),
                "doc_id": doc_id,
                "chunk_id": chunk_id,
                "subject": subject,
                "predicate": predicate,
                "value_raw": value_raw,
                "unit_raw": unit_raw,
                "time_scope_raw": time_raw,
                "qualifiers": json.dumps(qualifiers, ensure_ascii=False),
                "fact_type": str(item.get("fact_type") or parsed_value.dimension or "unknown")[:40],
                # Facts drawn from a page that tried to address the model are
                # kept but demoted, so they cannot lead a ranked view.
                "confidence": round(_as_float(item.get("confidence"), 0.6) * (0.5 if injection else 1.0), 3),
                "value_num": parsed_value.number,
                "value_unit": parsed_value.unit,
                "value_dim": parsed_value.dimension,
                "period_key": period.key,
                "period_start": period.start,
                "period_end": period.end,
                "period_basis": basis,
                "metric_key": key,
                "claim_text": display_claim(subject, predicate, value_raw, unit_raw, time_raw),
                "embed_text": canonical_claim(subject, predicate, qualifiers),
                "quote": quote,
                "page_index": page_index,
                "printed_page": printed_labels.get(page_index),
                "verification": verification,
                "verify_score": match.score,
                "quote_start": match.start,
                "quote_end": match.end,
                "created_at": now_iso(),
            }
        )

    return result
