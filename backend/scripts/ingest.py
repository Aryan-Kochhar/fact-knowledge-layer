"""Upload PDFs to a running server and follow the ingest until it finishes.

Goes through the same HTTP API the UI uses, so this exercises the real path
rather than a private shortcut.

Usage:
    python scripts/ingest.py <pdf-or-directory> [...] [--api http://127.0.0.1:8000]
    python scripts/ingest.py <dir> --dry-run       # list what would be sent
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

DEFAULT_API = "http://127.0.0.1:8000"


def collect(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            out.extend(sorted(path.rglob("*.pdf")))
        elif path.suffix.lower() == ".pdf" and path.is_file():
            out.append(path)
        else:
            print(f"  skipping {raw} (not a PDF or directory)")
    return out


def main(argv: list[str]) -> int:
    api = DEFAULT_API
    if "--api" in argv:
        i = argv.index("--api")
        api = argv[i + 1]
        del argv[i : i + 2]
    dry_run = "--dry-run" in argv
    if dry_run:
        argv.remove("--dry-run")

    pdfs = collect(argv)
    if not pdfs:
        print(__doc__)
        return 1

    print(f"{len(pdfs)} PDF(s) to send to {api}")
    for path in pdfs:
        print(f"  {path.name}  ({path.stat().st_size / 1e6:.1f} MB)")
    if dry_run:
        return 0

    with httpx.Client(base_url=api, timeout=300.0) as client:
        health = client.get("/api/health").json()
        print(
            f"\nserver: {health['keys_live']}/{health['keys_configured']} keys live · "
            f"extract={health['model']} · budget {health.get('budget_remaining')} left of "
            f"{health.get('daily_call_budget')}"
        )
        if not health["keys_live"]:
            print("no usable API keys - aborting")
            return 1

        # Sequential by default. Two documents ingesting at once would each build
        # their linking index before the other's facts existed, so they would
        # never be compared against each other - and their extraction lanes would
        # together exceed the key pool's rate. Going one at a time is also the
        # honest demonstration of incremental ingestion.
        started = time.time()
        for path in pdfs:
            with path.open("rb") as fh:
                response = client.post(
                    "/api/documents",
                    files=[("files", (path.name, fh, "application/pdf"))],
                )
            if response.status_code != 200:
                print(f"  {path.name}: HTTP {response.status_code} {response.text[:300]}")
                continue

            job_id = None
            for result in response.json()["results"]:
                status = result["status"]
                suffix = f" ({result.get('reason')})" if result.get("reason") else ""
                print(f"\n{result['filename']}: {status}{suffix}")
                if status == "queued":
                    job_id = result["job_id"]
            if not job_id:
                continue

            last_line = ""
            while True:
                time.sleep(2)
                job = client.get(f"/api/jobs/{job_id}").json()
                progress = f"{job['done']}/{job['total']}" if job["total"] else ""
                line = f"{job['stage']:<11} {progress:<9} {job.get('message') or ''}"
                if line != last_line:
                    print(f"  [{time.time() - started:6.0f}s] {line}")
                    last_line = line
                if job["status"] in ("done", "failed"):
                    if job["status"] == "failed":
                        print(f"  FAILED: {job.get('error')}")
                    break

        stats = client.get("/api/stats").json()
        health = client.get("/api/health").json()
        print(
            f"\ndone in {time.time() - started:.0f}s · "
            f"{stats['facts']} facts · {stats['relations']} relations · "
            f"{stats['issues']} issues · {health['calls_last_24h']} LLM calls used"
        )
        counts = stats.get("relation_counts", {})
        if counts:
            print("  " + " · ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
