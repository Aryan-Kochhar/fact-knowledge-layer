"""Live connectivity check: send one tiny request per key and report the result.

Costs one call per key. Never prints key values. Run this before a large ingest
so a bad key is discovered in seconds rather than 60 calls in.

Usage:  python scripts/check_keys.py [--model gemini-2.0-flash]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402

BASE = "https://generativelanguage.googleapis.com/v1beta"


def mask(key: str) -> str:
    return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "****"


async def probe(client: httpx.AsyncClient, key: str, model: str) -> tuple[str, str]:
    """Returns (status, detail)."""
    body = {
        "contents": [{"role": "user", "parts": [{"text": "Reply with the single word: ok"}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 8},
    }
    try:
        resp = await client.post(
            f"{BASE}/models/{model}:generateContent",
            params={"key": key},
            json=body,
            timeout=45.0,
        )
    except Exception as exc:
        return "NETWORK", str(exc)[:160]

    if resp.status_code == 200:
        try:
            parts = resp.json()["candidates"][0]["content"]["parts"]
            return "OK", "".join(p.get("text", "") for p in parts).strip()[:40]
        except Exception:
            return "OK", "(200, unusual body shape)"

    try:
        err = resp.json().get("error", {})
        detail = f"{err.get('status', '')} {err.get('message', '')}".strip()
    except Exception:
        detail = resp.text[:160]
    return str(resp.status_code), detail[:200]


async def list_models(client: httpx.AsyncClient, key: str) -> list[str]:
    try:
        resp = await client.get(f"{BASE}/models", params={"key": key}, timeout=45.0)
        if resp.status_code != 200:
            return []
        names = [m.get("name", "").removeprefix("models/") for m in resp.json().get("models", [])]
        return [n for n in names if "generateContent" not in n]
    except Exception:
        return []


async def main() -> int:
    model = settings.gemini_model
    args = sys.argv[1:]
    if "--model" in args:
        model = args[args.index("--model") + 1]

    keys = settings.gemini_keys
    if not keys:
        print("No keys configured in backend/.env (GEMINI_API_KEYS).")
        return 1

    print(f"Probing {len(keys)} key(s) against model '{model}'\n")
    ok = 0
    async with httpx.AsyncClient() as client:
        for i, key in enumerate(keys):
            status, detail = await probe(client, key, model)
            flag = "OK " if status == "OK" else "ERR"
            print(f"  [{i}] {mask(key):<16} {flag} {status:<10} {detail}")
            if status == "OK":
                ok += 1

        if ok == 0:
            print("\nNo key worked. Asking the API which models this key can see:")
            names = await list_models(client, keys[0])
            if names:
                print("  available:", ", ".join(sorted(names)[:25]))
            else:
                print("  could not list models either - the key is likely not a")
                print("  Generative Language API key, or that API is not enabled for its project.")

    print(f"\n{ok}/{len(keys)} key(s) usable.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
