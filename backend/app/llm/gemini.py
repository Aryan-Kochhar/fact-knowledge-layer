"""Gemini client over the raw REST API.

Deliberately not using `google-genai`: that SDK binds an API key at client
construction, so a rotating pool means rebuilding clients per call and losing
the connection pool. Talking to the endpoint directly with one shared httpx
client gives us exact control over which key is attached to which request, and
lets us read the 429 `RetryInfo` payload that drives key cooldowns.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from typing import Any

import httpx

from ..config import settings
from ..db import record_llm_call
from .keypool import KeyPool, NoKeysConfigured

log = logging.getLogger("fkl.gemini")

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Transient at the service level - retry on a different key.
_TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}


_KEY_IN_URL_RE = re.compile(r"([?&]key=)[^&\s\"'>]+")


def redact(text: object) -> str:
    """Strip API keys from anything that gets logged or persisted.

    The key travels as a query parameter, so an httpx transport error can carry
    the full request URL - key included - in its string form. That string is
    written to `llm_calls.error`, and that table lives in a database this repo
    commits. Redacting at every sink is cheaper than reasoning about which
    exception types embed a URL.
    """
    out = str(text)
    if not out:
        return out
    out = _KEY_IN_URL_RE.sub(r"\1<redacted>", out)
    for key in settings.gemini_keys:
        if key and len(key) > 8:
            out = out.replace(key, "<redacted>")
    return out


class GeminiError(RuntimeError):
    pass


class GeminiResponseError(GeminiError):
    """Model replied, but not with anything we could use."""


_pool: KeyPool | None = None
_client: httpx.AsyncClient | None = None


class _ModelHealth:
    """Circuit breaker for a whole model, not just a key.

    Free-tier daily quota is granted per model. When a model's allowance is
    spent, *every* key returns 429 for it, so retrying across the pool is pure
    waste: measured on a real run, each judgment call burned three attempts on an
    exhausted model before falling back, adding ~50s of latency per call and
    hundreds of pointless requests.

    So quota exhaustion is tracked per model. Once enough distinct keys have been
    refused, the model is skipped outright until its cooldown expires and callers
    go straight to the fallback.
    """

    # A model is considered spent once this many distinct keys have been refused.
    THRESHOLD = 2
    COOLDOWN_SECONDS = 900.0

    def __init__(self) -> None:
        self._exhausted_until: dict[str, float] = {}
        self._refused_keys: dict[str, set[int]] = {}

    def is_available(self, model: str) -> bool:
        until = self._exhausted_until.get(model)
        if until is None:
            return True
        if time.monotonic() >= until:
            self._exhausted_until.pop(model, None)
            self._refused_keys.pop(model, None)
            return True
        return False

    def record_quota_error(self, model: str, key_index: int) -> None:
        refused = self._refused_keys.setdefault(model, set())
        refused.add(key_index)
        if len(refused) >= self.THRESHOLD and model not in self._exhausted_until:
            self._exhausted_until[model] = time.monotonic() + self.COOLDOWN_SECONDS
            log.warning(
                "model %s appears to be out of daily quota (%d keys refused); "
                "skipping it for %.0fs",
                model,
                len(refused),
                self.COOLDOWN_SECONDS,
            )

    def record_success(self, model: str) -> None:
        self._refused_keys.pop(model, None)
        self._exhausted_until.pop(model, None)

    def snapshot(self) -> dict[str, float]:
        now = time.monotonic()
        return {
            model: round(max(0.0, until - now), 1)
            for model, until in self._exhausted_until.items()
        }


model_health = _ModelHealth()


def get_pool() -> KeyPool:
    global _pool
    if _pool is None:
        _pool = KeyPool(settings.gemini_keys, per_key_rpm=settings.per_key_rpm)
    return _pool


def reload_pool() -> KeyPool:
    """Rebuild the pool after keys change on disk (used by the /api/keys route)."""
    global _pool
    _pool = KeyPool(settings.gemini_keys, per_key_rpm=settings.per_key_rpm)
    return _pool


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.gemini_timeout_s, connect=15.0),
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


# --------------------------------------------------------------------------
# JSON recovery
# --------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text.strip()).strip()


def _balance_truncated(text: str) -> str | None:
    """Close a JSON document that was cut off mid-way by an output token cap.

    Walks the text tracking string state and bracket depth, rewinds to the last
    position where a value had cleanly finished, then appends the closers. This
    turns a truncated 40-fact response into a valid 37-fact response instead of
    losing the entire call's work.
    """
    stack: list[str] = []
    in_string = False
    escaped = False
    last_safe = -1

    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
                if stack and stack[-1] == "[":
                    last_safe = i
        elif ch == '"':
            in_string = True
        elif ch in "[{":
            stack.append(ch)
        elif ch in "]}":
            if not stack:
                return None
            stack.pop()
            last_safe = i
        elif ch == "," and not stack[-1:] == ["{"]:
            last_safe = i - 1

    if in_string or not stack:
        # Either mid-string (nothing safe to salvage) or already balanced.
        if not stack and not in_string:
            return text
    if last_safe < 0:
        return None

    head = text[: last_safe + 1]
    # Recompute the open stack for the truncated head.
    stack = []
    in_string = False
    escaped = False
    for ch in head:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "[{":
            stack.append(ch)
        elif ch in "]}":
            if stack:
                stack.pop()
    if in_string:
        return None
    closers = "".join("]" if c == "[" else "}" for c in reversed(stack))
    return head + closers


def parse_json_response(text: str) -> Any:
    """Parse model output into Python, repairing the failure modes we actually see."""
    cleaned = _strip_fences(text)
    if not cleaned:
        raise GeminiResponseError("empty response")

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Trim any prose before the first structural character.
    start = min([i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1], default=-1)
    if start > 0:
        cleaned = cleaned[start:]
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

    repaired = _balance_truncated(cleaned)
    if repaired:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    raise GeminiResponseError(f"unparseable JSON response (first 300 chars): {cleaned[:300]!r}")


# --------------------------------------------------------------------------
# call
# --------------------------------------------------------------------------

_RETRY_DELAY_RE = re.compile(r"(\d+(?:\.\d+)?)s")


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    header = resp.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    try:
        body = resp.json()
    except Exception:
        return None
    for detail in (body.get("error", {}) or {}).get("details", []) or []:
        delay = detail.get("retryDelay")
        if isinstance(delay, str):
            m = _RETRY_DELAY_RE.search(delay)
            if m:
                return float(m.group(1))
    return None


def _extract_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        feedback = payload.get("promptFeedback") or {}
        blocked = feedback.get("blockReason")
        raise GeminiResponseError(f"no candidates returned{f' (blocked: {blocked})' if blocked else ''}")
    cand = candidates[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts)
    if not text.strip():
        raise GeminiResponseError(f"empty candidate (finishReason={cand.get('finishReason')})")
    return text


async def generate_json(
    prompt: str,
    *,
    system: str | None = None,
    purpose: str = "generic",
    temperature: float = 0.1,
    max_output_tokens: int = 16384,
    model: str | None = None,
) -> Any:
    """Call Gemini and return parsed JSON, rotating keys across attempts.

    `max_output_tokens` has to cover the model's *thinking* tokens as well as its
    visible output on reasoning models, so it is set generously; the models in
    use allow far more than we ask for.
    """
    pool = get_pool()
    if len(pool) == 0:
        raise NoKeysConfigured("No Gemini API keys configured. Set GEMINI_API_KEYS in backend/.env")

    client = get_client()
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
        },
        # The starter documents are financial/economic filings; the default
        # safety filters occasionally trip on ordinary risk-factor language.
        "safetySettings": [
            {"category": c, "threshold": "BLOCK_NONE"}
            for c in (
                "HARM_CATEGORY_HARASSMENT",
                "HARM_CATEGORY_HATE_SPEECH",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "HARM_CATEGORY_DANGEROUS_CONTENT",
            )
        ],
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    primary_model = model or settings.gemini_model
    fallback_model = settings.gemini_fallback_model

    # If the primary is already known to be out of quota, do not spend attempts
    # rediscovering that - start on the fallback.
    if not model_health.is_available(primary_model) and fallback_model:
        primary_model = fallback_model

    model = primary_model
    started = time.perf_counter()
    last_error: Exception | None = None
    key_index: int | None = None

    for attempt in range(1, settings.max_attempts_per_call + 1):
        # Halfway through the attempt budget, fall back to a different model.
        # It has its own separate free-tier quota and its own load profile, so it
        # is a real escape hatch both when the primary's quota is exhausted across
        # the whole pool and when the primary is returning 503 under demand.
        if (
            attempt == settings.max_attempts_per_call // 2 + 1
            and fallback_model
            and fallback_model != primary_model
        ):
            model = fallback_model
            log.info("falling back to %s for %s after %d attempts", model, purpose, attempt - 1)
        # A model that just tripped its breaker should be abandoned immediately,
        # not at the halfway mark.
        elif not model_health.is_available(model) and fallback_model and model != fallback_model:
            model = fallback_model

        state = await pool.acquire()
        key_index = state.index
        url = f"{_BASE}/{model}:generateContent"
        try:
            resp = await client.post(url, params={"key": state.key}, json=body)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            await pool.report_error(state)
            await asyncio.sleep(min(2 ** attempt * 0.5, 8) + random.random())
            continue

        if resp.status_code == 200:
            try:
                text = _extract_text(resp.json())
                parsed = parse_json_response(text)
            except (GeminiResponseError, ValueError) as exc:
                last_error = exc
                await pool.report_error(state)
                # A malformed body is a model problem, not a key problem; a short
                # jittered pause then a fresh attempt is the cheapest fix.
                await asyncio.sleep(0.5 + random.random())
                continue

            await pool.report_success(state)
            model_health.record_success(model)
            record_llm_call(
                purpose=purpose,
                model=model,
                key_index=key_index,
                status="ok",
                attempts=attempt,
                latency_ms=int((time.perf_counter() - started) * 1000),
                in_chars=len(prompt),
                out_chars=len(text),
            )
            return parsed

        detail = resp.text[:400]
        if resp.status_code == 429:
            # Two different conditions share this status. A per-minute rate limit
            # is the key's problem and clears in seconds; an exhausted daily
            # allowance ("RESOURCE_EXHAUSTED") is the *model's* problem and will
            # not clear today, so parking the key would wrongly shrink the pool
            # for every other model too.
            if "RESOURCE_EXHAUSTED" in detail or "exceeded your current quota" in detail:
                model_health.record_quota_error(model, state.index)
            else:
                await pool.report_rate_limited(state, _retry_after_seconds(resp))
            last_error = GeminiError(f"429 rate limited on {model}: {detail}")
            continue

        if resp.status_code in (400, 401, 403) and (
            "API_KEY_INVALID" in detail or "API key not valid" in detail or resp.status_code in (401, 403)
        ):
            await pool.report_error(state, fatal=True, reason=f"HTTP {resp.status_code}")
            last_error = GeminiError(f"key {state.index} rejected: {detail}")
            log.warning("Disabling API key #%s: %s", state.index, redact(detail))
            continue

        if resp.status_code in _TRANSIENT_STATUS:
            await pool.report_error(state)
            last_error = GeminiError(f"HTTP {resp.status_code}: {detail}")
            await asyncio.sleep(min(2 ** attempt * 0.5, 8) + random.random())
            continue

        await pool.report_error(state)
        last_error = GeminiError(f"HTTP {resp.status_code}: {detail}")
        break

    record_llm_call(
        purpose=purpose,
        model=model,
        key_index=key_index,
        status="failed",
        attempts=settings.max_attempts_per_call,
        latency_ms=int((time.perf_counter() - started) * 1000),
        in_chars=len(prompt),
        out_chars=0,
        error=redact(last_error)[:500],
    )
    raise GeminiError(
        f"all {settings.max_attempts_per_call} attempts failed: {redact(last_error)}"
    )
