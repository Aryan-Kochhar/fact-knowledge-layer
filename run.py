#!/usr/bin/env python3
"""One-command start.

    python run.py

Creates the virtual environment if it is missing, installs dependencies, checks
your API key, and serves the whole app - API and UI - from a single process on
http://127.0.0.1:8000.

No Node required: the built frontend is committed and the backend serves it. Use
`npm run dev` in frontend/ only if you want to work on the UI itself.

Flags:
    --port N        serve on a different port
    --no-install    skip the dependency check (faster restarts)
    --fresh         start with an empty database instead of the sample corpus
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
VENV = BACKEND / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"


def say(message: str) -> None:
    print(f"  {message}", flush=True)


def run(*args: str, **kw) -> int:
    return subprocess.call(list(args), **kw)


def ensure_venv() -> None:
    if PY.is_file():
        return
    say(f"creating virtual environment in {VENV.relative_to(ROOT)}")
    venv.EnvBuilder(with_pip=True).create(VENV)


def deps_present() -> bool:
    probe = "import fastapi, fitz, httpx, numpy, sentence_transformers"
    return run(str(PY), "-c", probe, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


def install() -> bool:
    say("installing dependencies (a few minutes the first time)")
    run(str(PY), "-m", "pip", "install", "--upgrade", "pip", "--quiet")
    # CPU-only torch: the default wheel bundles CUDA and is an order of magnitude
    # larger for no benefit here.
    if run(str(PY), "-m", "pip", "install", "--index-url", TORCH_CPU_INDEX, "torch", "--quiet") != 0:
        say("could not install torch")
        return False
    if run(str(PY), "-m", "pip", "install", "-r", str(BACKEND / "requirements.txt"), "--quiet") != 0:
        say("could not install requirements")
        return False
    return True


def ensure_env_file() -> bool:
    """Returns True when at least one API key is configured."""
    env_path = BACKEND / ".env"
    if not env_path.is_file():
        shutil.copyfile(BACKEND / ".env.example", env_path)
        say(f"created {env_path.relative_to(ROOT)} from the template")

    # Parse rather than string-match: dotenv tolerates spaces and quotes around
    # the assignment ("GEMINI_API_KEYS = ..." is valid), so a naive startswith
    # check reports "no key" on a perfectly good file.
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip().upper() not in {"GEMINI_API_KEYS", "GEMINI_API_KEY"}:
            continue
        value = value.strip().strip("\"'")
        # The shipped template uses obvious placeholders; treat those as unset.
        if value and "key_one" not in value and len(value) > 20:
            return True
    return False


def main() -> int:
    args = sys.argv[1:]
    port = "8000"
    if "--port" in args:
        port = args[args.index("--port") + 1]

    print("\nFact Knowledge Layer\n")

    ensure_venv()
    if "--no-install" not in args and not deps_present():
        if not install():
            return 1

    if "--fresh" in args:
        os.environ["SEED_DB"] = ""
        say("starting with an empty database (--fresh)")
    elif not (ROOT / "data" / "facts.db").is_file():
        say("first run: seeding the pre-ingested corpus (6 documents, 3,460 facts)")

    has_key = ensure_env_file()
    dist = ROOT / "frontend" / "dist" / "index.html"

    print()
    if has_key:
        say("API key found - uploading new PDFs is enabled")
    else:
        say("no API key set. Everything already ingested is browsable;")
        say("uploading new PDFs needs a free key from https://aistudio.google.com/apikey")
        say(f"add it to {(BACKEND / '.env').relative_to(ROOT)} as GEMINI_API_KEYS=... and restart")

    if not dist.is_file():
        say("WARNING: frontend/dist is missing; only the JSON API will be served")
        say("build it with:  cd frontend && npm install && npm run build")

    print(f"\n  -> http://127.0.0.1:{port}\n")

    return run(
        str(PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", port,
        cwd=BACKEND,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nstopped")
