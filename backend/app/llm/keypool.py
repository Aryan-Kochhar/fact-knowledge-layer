"""Rotating pool of Gemini API keys.

The free tier limits requests per minute *per key*. With a pool of N keys the
effective ceiling is N x RPM, but only if we (a) spread load instead of
hammering key #1, and (b) take a key out of circulation the moment it returns
429 rather than retrying into the same wall.

The pool therefore tracks, per key:
  * a 60-second sliding window of request timestamps -> proactive rate limiting
  * a cooldown deadline set from 429 responses (honouring Retry-After)
  * consecutive auth failures -> permanently disable an invalid key

`acquire()` never fails: if every key is saturated it waits until the earliest
one frees up. Callers see a slow call, not an error.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _KeyState:
    index: int
    key: str
    window: deque[float] = field(default_factory=deque)
    cooldown_until: float = 0.0
    disabled: bool = False
    disabled_reason: str | None = None
    total_calls: int = 0
    total_errors: int = 0
    rate_limit_hits: int = 0

    def prune(self, now: float) -> None:
        while self.window and now - self.window[0] >= 60.0:
            self.window.popleft()

    def available_at(self, now: float, rpm: int) -> float:
        """Earliest wall-clock time this key may be used."""
        if self.disabled:
            return float("inf")
        self.prune(now)
        ready = self.cooldown_until
        if len(self.window) >= rpm:
            # Free again once the oldest call in the window ages out.
            ready = max(ready, self.window[0] + 60.0)
        return max(ready, now)

    def masked(self) -> str:
        if len(self.key) <= 8:
            return "****"
        return f"{self.key[:4]}…{self.key[-4:]}"


class NoKeysConfigured(RuntimeError):
    pass


class KeyPool:
    def __init__(self, keys: list[str], per_key_rpm: int = 12) -> None:
        # De-duplicate while preserving order: pasting the same key twice would
        # otherwise silently halve the pool's real capacity.
        seen: set[str] = set()
        unique: list[str] = []
        for k in keys:
            if k and k not in seen:
                seen.add(k)
                unique.append(k)
        self._states = [_KeyState(index=i, key=k) for i, k in enumerate(unique)]
        self.per_key_rpm = per_key_rpm
        self._lock = asyncio.Lock()
        self._cursor = 0

    def __len__(self) -> int:
        return len(self._states)

    @property
    def live_keys(self) -> int:
        return sum(1 for s in self._states if not s.disabled)

    async def acquire(self) -> _KeyState:
        """Reserve a slot on the least-loaded usable key, waiting if needed."""
        if not self._states:
            raise NoKeysConfigured(
                "No Gemini API keys configured. Set GEMINI_API_KEYS in backend/.env"
            )
        while True:
            async with self._lock:
                now = time.monotonic()
                usable = [s for s in self._states if not s.disabled]
                if not usable:
                    reasons = {s.disabled_reason for s in self._states if s.disabled_reason}
                    raise NoKeysConfigured(
                        "Every API key in the pool is disabled: " + "; ".join(sorted(filter(None, reasons)))
                    )

                # Round-robin start point so equal-cost keys share load evenly
                # instead of always favouring the first one in the list.
                ordered = usable[self._cursor % len(usable):] + usable[: self._cursor % len(usable)]
                best = min(ordered, key=lambda s: (s.available_at(now, self.per_key_rpm), len(s.window)))
                ready_at = best.available_at(now, self.per_key_rpm)

                if ready_at <= now:
                    best.window.append(now)
                    best.total_calls += 1
                    self._cursor = (self._cursor + 1) % max(len(usable), 1)
                    return best

                wait = min(ready_at - now, 5.0)

            # Sleep outside the lock so other tasks can still inspect the pool.
            await asyncio.sleep(max(wait, 0.05))

    async def report_rate_limited(self, state: _KeyState, retry_after: float | None) -> None:
        async with self._lock:
            # Default 35s: long enough to clear a per-minute bucket without
            # parking the key for a full minute when the server gave no hint.
            delay = retry_after if retry_after and retry_after > 0 else 35.0
            state.cooldown_until = max(state.cooldown_until, time.monotonic() + delay)
            state.rate_limit_hits += 1
            state.total_errors += 1
            # Treat the key as fully spent for this minute.
            state.window.extend([time.monotonic()] * max(0, self.per_key_rpm - len(state.window)))

    async def report_error(self, state: _KeyState, *, fatal: bool = False, reason: str | None = None) -> None:
        async with self._lock:
            state.total_errors += 1
            if fatal:
                state.disabled = True
                state.disabled_reason = reason or "rejected by API"

    async def report_success(self, state: _KeyState) -> None:  # noqa: ARG002 - symmetry
        return None

    def snapshot(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        out = []
        for s in self._states:
            s.prune(now)
            cooldown = max(0.0, s.cooldown_until - now)
            out.append(
                {
                    "index": s.index,
                    "key": s.masked(),
                    "disabled": s.disabled,
                    "disabled_reason": s.disabled_reason,
                    "in_flight_window": len(s.window),
                    "rpm_limit": self.per_key_rpm,
                    "cooldown_seconds": round(cooldown, 1),
                    "total_calls": s.total_calls,
                    "total_errors": s.total_errors,
                    "rate_limit_hits": s.rate_limit_hits,
                }
            )
        return out
