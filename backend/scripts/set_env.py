"""Update settings in backend/.env in place, without reading secrets aloud.

Replaces a KEY=value line if present, appends it otherwise. Everything not named
on the command line is left byte-for-byte alone, so API keys are never touched,
rewritten or printed.

Usage:  python scripts/set_env.py GEMINI_MODEL=gemini-3.5-flash-lite DAILY_CALL_BUDGET=900
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
# Suffix match, not substring: GEMINI_PER_KEY_RPM is a tuning knob, not a secret.
SECRET_SUFFIXES = ("_KEY", "_KEYS", "_TOKEN", "_TOKENS", "_SECRET", "_PASSWORD", "_CREDENTIALS")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    if not ENV_PATH.is_file():
        print(f"no .env at {ENV_PATH}")
        return 1

    pairs: dict[str, str] = {}
    for arg in argv:
        if "=" not in arg:
            print(f"skipping malformed argument: {arg!r}")
            continue
        name, value = arg.split("=", 1)
        name = name.strip()
        if name.upper().endswith(SECRET_SUFFIXES):
            print(f"refusing to set {name} from the command line - edit .env directly for secrets")
            return 1
        pairs[name] = value.strip()

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()

    for i, line in enumerate(lines):
        match = re.match(r"^\s*([A-Z0-9_]+)\s*=", line)
        if not match:
            continue
        name = match.group(1)
        if name in pairs:
            lines[i] = f"{name}={pairs[name]}"
            seen.add(name)

    appended = [name for name in pairs if name not in seen]
    if appended:
        lines.append("")
        lines.append("# --- set by scripts/set_env.py ---")
        lines.extend(f"{name}={pairs[name]}" for name in appended)

    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    for name, value in pairs.items():
        where = "updated" if name in seen else "added"
        print(f"  {where:<8} {name}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
