"""Report what the server will load from .env, without ever printing key values.

Usage:  python scripts/check_env.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BACKEND_DIR, settings  # noqa: E402


def mask(key: str) -> str:
    return f"{key[:4]}…{key[-4:]} ({len(key)} chars)" if len(key) > 10 else "**short**"


def main() -> int:
    env_path = BACKEND_DIR / ".env"
    print(f".env present: {env_path.is_file()}  ({env_path})")

    keys = settings.gemini_keys
    print(f"\nkeys loaded : {len(keys)}")
    for i, key in enumerate(keys):
        # Google issues both the legacy "AIza..." keys and the newer "AQ." format.
        # Shape alone proves nothing either way - scripts/check_keys.py is the
        # only real test, since it actually calls the API.
        shape = "AIza" if key.startswith("AIza") else ("AQ." if key.startswith("AQ.") else "unrecognised prefix")
        print(f"  [{i}] {mask(key):<28} {shape}")

    duplicates = len(keys) - len(set(keys))
    if duplicates:
        print(f"\n  WARNING: {duplicates} duplicate key(s) - the pool de-duplicates, so real capacity is lower.")

    print("\nconfig")
    print(f"  model                : {settings.gemini_model}")
    print(f"  fallback model       : {settings.gemini_fallback_model}")
    print(f"  per-key rpm          : {settings.per_key_rpm}")
    print(f"  pool rpm             : {settings.per_key_rpm * max(len(keys), 1)}")
    print(f"  daily call budget    : {settings.daily_call_budget or 'disabled'}")
    print(f"  chunk target / max   : {settings.chunk_target_chars:,} / {settings.chunk_max_chars:,} chars")
    print(f"  max facts per chunk  : {settings.max_facts_per_chunk}")
    print(f"  judge batch size     : {settings.judge_batch_size}")
    print(f"  similarity threshold : {settings.similarity_threshold}")
    print(f"  data dir             : {settings.data_dir}")
    return 0 if keys else 1


if __name__ == "__main__":
    raise SystemExit(main())
