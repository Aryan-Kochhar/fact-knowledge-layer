"""HTTP API.

Read endpoints are plain SQL projections. The one write endpoint (upload) does
the minimum synchronously - persist the file, dedupe by content hash, create a
job - and hands the long pipeline to a background task so the request returns in
milliseconds and the UI can poll job progress.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, UploadFile

from ..config import settings
from ..db import calls_in_last_day, execute, new_id, query, query_one
from ..pipeline import showcase as showcase_module
from ..pipeline.ingest import create_job, ingest_document, register_document

log = logging.getLogger("fkl.api")
router = APIRouter(prefix="/api")

# Hold strong references so in-flight ingest tasks are not garbage collected.
_RUNNING: set[asyncio.Task] = set()

_FACT_SELECT = """
    SELECT f.*, d.filename, d.title AS doc_title
    FROM facts f JOIN documents d ON d.id = f.doc_id
"""


def _decode_fact(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("qualifiers")
    if isinstance(raw, str):
        try:
            row["qualifiers"] = json.loads(raw)
        except json.JSONDecodeError:
            row["qualifiers"] = []
    return row


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", (name or "document.pdf").strip())
    return (cleaned[:180] or "document.pdf")


# --------------------------------------------------------------------------
# system
# --------------------------------------------------------------------------

@router.get("/health")
def health() -> dict[str, Any]:
    from ..llm.gemini import get_pool

    pool = get_pool()
    used = calls_in_last_day()
    return {
        "status": "ok",
        "keys_configured": len(pool),
        "keys_live": pool.live_keys,
        "model": settings.gemini_model,
        "fallback_model": settings.gemini_fallback_model,
        "embedding_model": settings.embedding_model,
        "documents": query("SELECT COUNT(*) AS n FROM documents")[0]["n"],
        "facts": query("SELECT COUNT(*) AS n FROM facts")[0]["n"],
        "calls_last_24h": used,
        "daily_call_budget": settings.daily_call_budget,
        "budget_remaining": max(0, settings.daily_call_budget - used) if settings.daily_call_budget else None,
        "demo_mode": settings.demo_mode,
        "max_upload_mb": settings.max_upload_mb,
    }


@router.get("/keys")
def keys() -> dict[str, Any]:
    """Key-pool telemetry. Keys are masked; the raw values never leave the server."""
    from ..llm.gemini import get_pool, model_health

    pool = get_pool()
    return {
        "per_key_rpm": pool.per_key_rpm,
        "keys": pool.snapshot(),
        # Models currently skipped because their daily quota looks spent.
        "models_cooling_down": model_health.snapshot(),
        "models": {
            "extract": settings.gemini_model,
            "judge": settings.gemini_judge_model,
            "escalation": settings.gemini_escalation_model,
            "fallback": settings.gemini_fallback_model,
        },
    }


@router.get("/stats")
def stats() -> dict[str, Any]:
    relation_counts = {
        r["relation"]: r["n"]
        for r in query("SELECT relation, COUNT(*) AS n FROM relations GROUP BY relation")
    }
    verification = {
        r["verification"]: r["n"]
        for r in query("SELECT verification, COUNT(*) AS n FROM facts GROUP BY verification")
    }
    calls = query(
        """SELECT purpose, status, COUNT(*) AS n, AVG(latency_ms) AS avg_ms
           FROM llm_calls GROUP BY purpose, status"""
    )
    return {
        "documents": query("SELECT COUNT(*) AS n FROM documents")[0]["n"],
        "pages": query("SELECT COUNT(*) AS n FROM pages")[0]["n"],
        "facts": query("SELECT COUNT(*) AS n FROM facts")[0]["n"],
        "facts_with_numbers": query("SELECT COUNT(*) AS n FROM facts WHERE value_num IS NOT NULL")[0]["n"],
        "relations": query("SELECT COUNT(*) AS n FROM relations")[0]["n"],
        "relation_counts": relation_counts,
        "verification": verification,
        "issues": query("SELECT COUNT(*) AS n FROM issues")[0]["n"],
        "llm_calls": calls,
        "top_periods": query(
            """SELECT period_key, COUNT(*) AS n FROM facts
               WHERE period_key IS NOT NULL GROUP BY period_key ORDER BY n DESC LIMIT 12"""
        ),
    }


# --------------------------------------------------------------------------
# documents
# --------------------------------------------------------------------------

@router.post("/documents")
async def upload_documents(
    background: BackgroundTasks,  # noqa: ARG001 - kept for API symmetry
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    from ..llm.gemini import get_pool

    if settings.demo_mode:
        raise HTTPException(
            status_code=403,
            detail=(
                "This deployment is read-only. It serves a corpus that was ingested ahead of "
                "time so the results can be explored without an API key. Clone the repository "
                "and run it locally to ingest your own PDFs."
            ),
        )

    if len(get_pool()) == 0:
        raise HTTPException(
            status_code=503,
            detail="No Gemini API keys configured. Add GEMINI_API_KEYS to backend/.env and restart.",
        )

    # Free-tier daily caps are per key and reset on a rolling window. Refusing
    # here is far kinder than starting a 60-call ingest that dies a third of the
    # way through and leaves a half-extracted document behind.
    if settings.daily_call_budget:
        used = calls_in_last_day()
        if used >= settings.daily_call_budget:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Daily call budget reached ({used}/{settings.daily_call_budget} in the last 24h). "
                    "Raise DAILY_CALL_BUDGET in backend/.env, add more keys, or wait for the window to roll."
                ),
            )

    accepted: list[dict[str, Any]] = []
    for upload in files:
        name = _safe_filename(upload.filename or "document.pdf")
        if not name.lower().endswith(".pdf"):
            accepted.append({"filename": name, "status": "rejected", "reason": "not a PDF"})
            continue

        payload = await upload.read()
        if not payload:
            accepted.append({"filename": name, "status": "rejected", "reason": "empty file"})
            continue
        if not payload.startswith(b"%PDF"):
            accepted.append({"filename": name, "status": "rejected", "reason": "not a valid PDF (bad header)"})
            continue
        if settings.max_upload_mb and len(payload) > settings.max_upload_mb * 1_000_000:
            accepted.append(
                {
                    "filename": name,
                    "status": "rejected",
                    "reason": f"larger than the {settings.max_upload_mb} MB limit",
                }
            )
            continue

        target = settings.upload_dir / f"{new_id()}_{name}"
        target.write_bytes(payload)

        doc_id, existed = register_document(target, name)
        if existed:
            target.unlink(missing_ok=True)
            accepted.append(
                {
                    "filename": name,
                    "doc_id": doc_id,
                    "status": "duplicate",
                    "reason": "identical content already ingested",
                }
            )
            continue

        job_id = create_job(doc_id)
        task = asyncio.create_task(
            ingest_document(job_id=job_id, doc_id=doc_id, path=target, filename=name)
        )
        _RUNNING.add(task)
        task.add_done_callback(_RUNNING.discard)
        accepted.append({"filename": name, "doc_id": doc_id, "job_id": job_id, "status": "queued"})

    return {"results": accepted}


@router.get("/documents")
def list_documents() -> list[dict[str, Any]]:
    return query(
        """SELECT d.*,
                  (SELECT COUNT(*) FROM facts f WHERE f.doc_id = d.id) AS fact_count,
                  (SELECT COUNT(*) FROM issues i WHERE i.doc_id = d.id) AS issue_count,
                  (SELECT id FROM jobs j WHERE j.doc_id = d.id ORDER BY created_at DESC LIMIT 1) AS latest_job
           FROM documents d
           ORDER BY d.created_at DESC"""
    )


@router.get("/documents/{doc_id}")
def get_document(doc_id: str) -> dict[str, Any]:
    doc = query_one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if not doc:
        raise HTTPException(404, "document not found")
    if doc.get("profile"):
        try:
            doc["profile"] = json.loads(doc["profile"])
        except json.JSONDecodeError:
            pass
    doc["fact_count"] = query("SELECT COUNT(*) AS n FROM facts WHERE doc_id = ?", (doc_id,))[0]["n"]
    doc["chunks"] = query(
        "SELECT status, COUNT(*) AS n FROM chunks WHERE doc_id = ? GROUP BY status", (doc_id,)
    )
    doc["issues"] = query(
        "SELECT kind, COUNT(*) AS n FROM issues WHERE doc_id = ? GROUP BY kind ORDER BY n DESC", (doc_id,)
    )
    doc["pages_with_labels"] = query(
        "SELECT COUNT(*) AS n FROM pages WHERE doc_id = ? AND printed_label IS NOT NULL", (doc_id,)
    )[0]["n"]
    return doc


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str) -> dict[str, Any]:
    """Remove a document. Cascades take out its facts, embeddings and exactly the
    relations that referenced them - other documents are untouched."""
    doc = query_one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if not doc:
        raise HTTPException(404, "document not found")
    facts = query("SELECT COUNT(*) AS n FROM facts WHERE doc_id = ?", (doc_id,))[0]["n"]
    relations = query(
        """SELECT COUNT(*) AS n FROM relations
           WHERE fact_a IN (SELECT id FROM facts WHERE doc_id = ?)
              OR fact_b IN (SELECT id FROM facts WHERE doc_id = ?)""",
        (doc_id, doc_id),
    )[0]["n"]
    execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    stored = Path(str(doc.get("stored_path") or ""))
    try:
        if stored.is_absolute() and stored.is_file():
            stored.unlink()
    except OSError:
        log.warning("could not remove stored file for %s", doc_id)

    return {"deleted": doc_id, "facts_removed": facts, "relations_removed": relations}


@router.get("/documents/{doc_id}/pages/{page_index}")
def get_page(doc_id: str, page_index: int) -> dict[str, Any]:
    page = query_one(
        "SELECT * FROM pages WHERE doc_id = ? AND page_index = ?", (doc_id, page_index)
    )
    if not page:
        raise HTTPException(404, "page not found")
    return page


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------

@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not job:
        raise HTTPException(404, "job not found")
    return job


@router.get("/jobs")
def list_jobs(limit: int = Query(20, ge=1, le=200)) -> list[dict[str, Any]]:
    return query(
        """SELECT j.*, d.filename FROM jobs j LEFT JOIN documents d ON d.id = j.doc_id
           ORDER BY j.created_at DESC LIMIT ?""",
        (limit,),
    )


# --------------------------------------------------------------------------
# facts
# --------------------------------------------------------------------------

@router.get("/facts")
def list_facts(
    doc_id: str | None = None,
    q: str | None = None,
    period: str | None = None,
    verification: str | None = None,
    numeric_only: bool = False,
    linked_only: bool = False,
    limit: int = Query(60, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []

    if doc_id:
        where.append("f.doc_id = ?")
        params.append(doc_id)
    if q:
        where.append("(f.subject LIKE ? OR f.predicate LIKE ? OR f.value_raw LIKE ? OR f.quote LIKE ?)")
        params.extend([f"%{q}%"] * 4)
    if period:
        where.append("f.period_key = ?")
        params.append(period)
    if verification:
        where.append("f.verification = ?")
        params.append(verification)
    if numeric_only:
        where.append("f.value_num IS NOT NULL")
    if linked_only:
        where.append(
            "EXISTS (SELECT 1 FROM relations r WHERE (r.fact_a = f.id OR r.fact_b = f.id) "
            "AND r.relation != 'unrelated')"
        )

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    total = query(f"SELECT COUNT(*) AS n FROM facts f {clause}", tuple(params))[0]["n"]
    rows = query(
        f"""{_FACT_SELECT} {clause}
            ORDER BY f.confidence DESC, f.page_index ASC
            LIMIT ? OFFSET ?""",
        tuple(params) + (limit, offset),
    )
    facts = [_decode_fact(r) for r in rows]

    # Attach relation counts in one pass rather than N queries.
    if facts:
        ids = [f["id"] for f in facts]
        placeholders = ",".join("?" * len(ids))
        counts = query(
            f"""SELECT fact_id, relation, COUNT(*) AS n FROM (
                    SELECT fact_a AS fact_id, relation FROM relations WHERE fact_a IN ({placeholders})
                    UNION ALL
                    SELECT fact_b AS fact_id, relation FROM relations WHERE fact_b IN ({placeholders})
                ) GROUP BY fact_id, relation""",
            tuple(ids) * 2,
        )
        by_fact: dict[str, dict[str, int]] = {}
        for row in counts:
            by_fact.setdefault(row["fact_id"], {})[row["relation"]] = row["n"]
        for fact in facts:
            fact["relation_counts"] = by_fact.get(fact["id"], {})

    return {"total": total, "limit": limit, "offset": offset, "facts": facts}


@router.get("/facts/{fact_id}")
def get_fact(fact_id: str) -> dict[str, Any]:
    row = query_one(f"{_FACT_SELECT} WHERE f.id = ?", (fact_id,))
    if not row:
        raise HTTPException(404, "fact not found")
    fact = _decode_fact(row)

    page = query_one(
        "SELECT page_index, printed_label, text FROM pages WHERE doc_id = ? AND page_index = ?",
        (fact["doc_id"], fact["page_index"]),
    )
    fact["evidence"] = {
        "page_index": fact["page_index"],
        "printed_page": fact["printed_page"],
        "quote": fact["quote"],
        "quote_start": fact["quote_start"],
        "quote_end": fact["quote_end"],
        "verification": fact["verification"],
        "verify_score": fact["verify_score"],
        "page_text": page["text"] if page else None,
    }

    related = query(
        "SELECT * FROM relations WHERE fact_a = ? OR fact_b = ? ORDER BY confidence DESC",
        (fact_id, fact_id),
    )
    hydrated = showcase_module.hydrate_relations(related)
    # Orient every relation so "a" is always the fact being viewed.
    for rel in hydrated:
        if rel["a"]["id"] != fact_id:
            rel["a"], rel["b"] = rel["b"], rel["a"]
    fact["relations"] = hydrated
    return fact


# --------------------------------------------------------------------------
# relations / showcase / issues
# --------------------------------------------------------------------------

@router.get("/relations")
def list_relations(
    relation: str | None = None,
    doc_id: str | None = None,
    cross_doc_only: bool = False,
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    limit: int = Query(50, ge=1, le=300),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    where = ["r.confidence >= ?"]
    params: list[Any] = [min_confidence]

    if relation:
        where.append("r.relation = ?")
        params.append(relation)
    else:
        where.append("r.relation != 'unrelated'")
    if cross_doc_only:
        where.append("r.cross_doc = 1")
    if doc_id:
        where.append(
            "(EXISTS (SELECT 1 FROM facts fa WHERE fa.id = r.fact_a AND fa.doc_id = ?) "
            "OR EXISTS (SELECT 1 FROM facts fb WHERE fb.id = r.fact_b AND fb.doc_id = ?))"
        )
        params.extend([doc_id, doc_id])

    clause = f"WHERE {' AND '.join(where)}"
    total = query(f"SELECT COUNT(*) AS n FROM relations r {clause}", tuple(params))[0]["n"]
    rows = query(
        f"""SELECT r.* FROM relations r {clause}
            ORDER BY
              CASE r.relation WHEN 'contradicts' THEN 0 WHEN 'reconcilable_context' THEN 1
                              WHEN 'corroborates' THEN 2 ELSE 3 END,
              r.confidence DESC
            LIMIT ? OFFSET ?""",
        tuple(params) + (limit, offset),
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "relations": showcase_module.hydrate_relations(rows),
    }


@router.get("/showcase")
def get_showcase() -> dict[str, Any]:
    return showcase_module.build_showcase()


@router.get("/issues")
def list_issues(
    doc_id: str | None = None,
    kind: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    where, params = [], []
    if doc_id:
        where.append("i.doc_id = ?")
        params.append(doc_id)
    if kind:
        where.append("i.kind = ?")
        params.append(kind)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = query(
        f"""SELECT i.*, d.filename FROM issues i LEFT JOIN documents d ON d.id = i.doc_id
            {clause} ORDER BY i.created_at DESC LIMIT ?""",
        tuple(params) + (limit,),
    )
    for row in rows:
        if row.get("payload"):
            try:
                row["payload"] = json.loads(row["payload"])
            except json.JSONDecodeError:
                pass
    return rows
