"""End-to-end ingestion of one document.

    parse -> profile -> chunk -> extract -> verify -> embed -> link -> judge

Incremental by construction. Ingesting document N never re-parses, re-extracts
or re-embeds documents 1..N-1: their facts and vectors are already in the store,
so the only new work is this document's own extraction plus the cross-document
comparisons that this document makes newly possible. Deleting a document
removes its facts and, by cascade, exactly the relations that involved them.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from ..config import settings
from ..db import execute, execute_many, get_conn, new_id, now_iso, query, record_issue, update_job
from .chunking import build_chunks
from .embeddings import VectorIndex, embed_texts, to_blob
from .extract import extract_from_chunk, profile_document
from .linking import build_candidates, filter_candidates, judge_batch, load_facts, needs_escalation
from .pdf_parse import document_opening, parse_pdf

log = logging.getLogger("fkl.ingest")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _persist_facts(facts: list[dict[str, Any]]) -> None:
    if not facts:
        return
    columns = [
        "id", "doc_id", "chunk_id", "subject", "predicate", "value_raw", "unit_raw",
        "time_scope_raw", "qualifiers", "fact_type", "confidence", "value_num",
        "value_unit", "value_dim", "period_key", "period_start", "period_end",
        "period_basis", "metric_key", "claim_text", "quote", "page_index",
        "printed_page", "verification", "verify_score", "quote_start", "quote_end",
        "created_at",
    ]
    sql = f"INSERT OR REPLACE INTO facts ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})"
    execute_many(sql, [tuple(f.get(c) for c in columns) for f in facts])


async def _run_extraction(
    *,
    doc_id: str,
    job_id: str,
    chunk_rows: list[dict[str, Any]],
    page_texts: dict[int, str],
    printed_labels: dict[int, str | None],
    profile: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Extract from every chunk, bounded concurrency, failures isolated per chunk."""
    from ..llm.gemini import get_pool

    # Never run more concurrent calls than the key pool can absorb: extra
    # concurrency past that point just queues inside the pool.
    lanes = max(1, min(settings.extraction_concurrency, max(len(get_pool()), 1) * 2))
    semaphore = asyncio.Semaphore(lanes)
    all_facts: list[dict[str, Any]] = []
    completed = 0
    lock = asyncio.Lock()

    async def one(chunk: dict[str, Any]) -> None:
        nonlocal completed
        async with semaphore:
            scoped_pages = {
                p: text
                for p, text in page_texts.items()
                if chunk["page_start"] <= p <= chunk["page_end"]
            }
            try:
                result = await extract_from_chunk(
                    doc_id=doc_id,
                    chunk_id=chunk["id"],
                    chunk_text=chunk["text"],
                    page_texts=scoped_pages,
                    printed_labels=printed_labels,
                    profile=profile,
                )
            except Exception as exc:  # one bad chunk must not sink the document
                log.warning("chunk %s failed: %s", chunk["id"], exc)
                execute(
                    "UPDATE chunks SET status = 'failed', error = ? WHERE id = ?",
                    (str(exc)[:500], chunk["id"]),
                )
                record_issue(
                    doc_id=doc_id,
                    chunk_id=chunk["id"],
                    kind="chunk_failed",
                    severity="error",
                    detail=f"extraction failed for pages {chunk['page_start']}–{chunk['page_end']}: {exc}",
                )
            else:
                _persist_facts(result.facts)
                for issue in result.issues:
                    record_issue(
                        doc_id=doc_id,
                        chunk_id=chunk["id"],
                        kind=issue["kind"],
                        severity=issue.get("severity", "warning"),
                        detail=issue["detail"],
                        payload=issue.get("payload"),
                    )
                execute(
                    "UPDATE chunks SET status = 'done', fact_count = ? WHERE id = ?",
                    (len(result.facts), chunk["id"]),
                )
                async with lock:
                    all_facts.extend(result.facts)
            finally:
                async with lock:
                    completed += 1
                    update_job(
                        job_id,
                        done=completed,
                        message=f"extracted {len(all_facts)} facts from {completed}/{len(chunk_rows)} chunks",
                    )

    await asyncio.gather(*(one(c) for c in chunk_rows))
    return all_facts


async def _run_linking(job_id: str, new_facts: list[dict[str, Any]]) -> int:
    """Compare new facts against everything already known."""
    if not new_facts:
        return 0

    update_job(job_id, stage="linking", done=0, total=0, message="searching for related facts")

    fact_lookup = load_facts()
    index = VectorIndex.load()
    rows = query(
        "SELECT fact_id, dim, vec FROM fact_embeddings WHERE fact_id IN (%s)"
        % ",".join("?" * len(new_facts)),
        tuple(f["id"] for f in new_facts),
    )
    from .embeddings import from_blob

    vectors = {r["fact_id"]: from_blob(r["vec"], r["dim"]) for r in rows}

    # Use the stored rows (they carry document metadata the judge prompt wants).
    hydrated = [fact_lookup[f["id"]] for f in new_facts if f["id"] in fact_lookup]

    candidates = build_candidates(hydrated, index, vectors, fact_lookup)
    keep, rejected, dropped = filter_candidates(candidates)
    log.info(
        "linking: %d candidates -> %d judged, %d rejected by rules, %d dropped for budget",
        len(candidates),
        len(keep),
        len(rejected),
        dropped,
    )
    if dropped:
        # Recorded, not hidden: a reader should be able to see that the graph is
        # a ranked subset rather than exhaustive.
        record_issue(
            doc_id=new_facts[0]["doc_id"],
            chunk_id=None,
            kind="pairs_over_budget",
            severity="info",
            detail=(
                f"{len(candidates)} candidate pairs were generated; the top {len(keep)} by "
                f"priority were judged and {dropped} lower-priority pairs were skipped to stay "
                f"within MAX_PAIRS_PER_INGEST={settings.max_pairs_per_ingest}."
            ),
        )

    if not keep:
        update_job(job_id, message="no comparable facts found in other documents")
        return 0

    # Route the hard pairs to the stronger model and the rest to the workhorse,
    # batching each group separately so a batch is never mixed.
    hard = [c for c in keep if needs_escalation(c)]
    routine = [c for c in keep if not needs_escalation(c)]

    def batched(items: list[dict[str, Any]], model: str) -> list[tuple[list[dict[str, Any]], str]]:
        size = settings.judge_batch_size
        return [(items[i : i + size], model) for i in range(0, len(items), size)]

    batches = batched(hard, settings.gemini_escalation_model) + batched(
        routine, settings.gemini_judge_model
    )
    log.info(
        "judging %d pairs in %d batches (%d escalated to %s)",
        len(keep),
        len(batches),
        len(hard),
        settings.gemini_escalation_model,
    )
    update_job(
        job_id,
        total=len(batches),
        message=f"judging {len(keep)} candidate pairs ({len(hard)} escalated)",
    )

    from ..llm.gemini import get_pool

    lanes = max(1, min(settings.extraction_concurrency, max(len(get_pool()), 1) * 2))
    semaphore = asyncio.Semaphore(lanes)
    stored = 0
    completed = 0
    lock = asyncio.Lock()

    async def one(batch: list[dict[str, Any]], model: str) -> None:
        nonlocal stored, completed
        async with semaphore:
            try:
                relation_rows = await judge_batch(batch, model=model)
            except Exception as exc:
                log.warning("judge batch failed: %s", exc)
                record_issue(
                    doc_id=batch[0]["a"]["doc_id"],
                    chunk_id=None,
                    kind="judge_failed",
                    severity="error",
                    detail=f"relationship judgment failed for {len(batch)} pairs: {exc}",
                )
                relation_rows = []

            for row in relation_rows:
                if not row.get("_problems"):
                    continue
                fact_a, fact_b = row["_pair"]
                record_issue(
                    doc_id=fact_a["doc_id"],
                    chunk_id=None,
                    kind="judgment_inconsistent",
                    severity="warning",
                    detail=(
                        f"'{fact_a['subject']} — {fact_a['predicate']}' vs "
                        f"'{fact_b['subject']} — {fact_b['predicate']}': "
                        + "; ".join(row["_problems"])
                        + ". Confidence was halved and the verdict flagged for review."
                    ),
                    payload={
                        "relation": row["relation"],
                        "reasoning": row["reasoning"],
                        "a": {
                            "value": fact_a.get("value_raw"),
                            "period": fact_a.get("period_key"),
                            "quote": fact_a.get("quote"),
                        },
                        "b": {
                            "value": fact_b.get("value_raw"),
                            "period": fact_b.get("period_key"),
                            "quote": fact_b.get("quote"),
                        },
                    },
                )

            if relation_rows:
                with get_conn() as conn:
                    for row in relation_rows:
                        conn.execute(
                            """INSERT OR IGNORE INTO relations
                               (id, fact_a, fact_b, cross_doc, relation, reasoning, reconciliation,
                                dimension, confidence, raw_confidence, similarity, decided_by, created_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                row["id"], row["fact_a"], row["fact_b"], row["cross_doc"],
                                row["relation"], row["reasoning"], row["reconciliation"],
                                row["dimension"], row["confidence"], row["raw_confidence"],
                                row["similarity"], row["decided_by"], row["created_at"],
                            ),
                        )
            async with lock:
                stored += len(relation_rows)
                completed += 1
                update_job(job_id, done=completed, message=f"judged {completed}/{len(batches)} batches")

    await asyncio.gather(*(one(batch, model) for batch, model in batches))
    return stored


async def ingest_document(*, job_id: str, doc_id: str, path: Path, filename: str) -> None:
    """Full pipeline for one uploaded PDF. Updates the job row as it goes."""
    try:
        # ---------------- parse ----------------
        update_job(job_id, status="running", stage="parsing", message="extracting text and tables")
        pages = await asyncio.to_thread(parse_pdf, path)
        if not pages:
            raise ValueError("no pages could be read from this PDF")

        execute_many(
            "INSERT OR REPLACE INTO pages (doc_id, page_index, printed_label, text, char_count) VALUES (?, ?, ?, ?, ?)",
            [(doc_id, p.page_index, p.printed_label, p.text, p.char_count) for p in pages],
        )
        execute("UPDATE documents SET page_count = ? WHERE id = ?", (len(pages), doc_id))

        total_chars = sum(p.char_count for p in pages)
        if total_chars < 200:
            record_issue(
                doc_id=doc_id,
                chunk_id=None,
                kind="no_text_layer",
                severity="error",
                detail=(
                    f"{filename} yielded only {total_chars} characters of text across {len(pages)} pages. "
                    "It is most likely a scanned/image PDF with no text layer; OCR would be required."
                ),
            )

        # ---------------- profile ----------------
        update_job(job_id, stage="profiling", message="inferring document context")
        profile = await profile_document(filename, document_opening(pages))
        if profile:
            execute(
                "UPDATE documents SET title = ?, profile = ? WHERE id = ?",
                (
                    str(profile.get("title") or filename)[:300],
                    json.dumps(profile, ensure_ascii=False),
                    doc_id,
                ),
            )
        else:
            record_issue(
                doc_id=doc_id,
                chunk_id=None,
                kind="profile_failed",
                severity="warning",
                detail=(
                    "Could not infer document context from the opening pages. Facts will only "
                    "carry a period where their own text states one."
                ),
            )

        # ---------------- chunk ----------------
        chunks = build_chunks(pages)
        chunk_rows = [
            {
                "id": new_id("c_"),
                "doc_id": doc_id,
                "ordinal": c.ordinal,
                "page_start": c.page_start,
                "page_end": c.page_end,
                "text": c.text,
                "char_count": c.char_count,
            }
            for c in chunks
        ]
        execute_many(
            "INSERT OR REPLACE INTO chunks (id, doc_id, ordinal, page_start, page_end, char_count, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            [(c["id"], c["doc_id"], c["ordinal"], c["page_start"], c["page_end"], c["char_count"]) for c in chunk_rows],
        )

        # ---------------- extract ----------------
        update_job(
            job_id,
            stage="extracting",
            done=0,
            total=len(chunk_rows),
            message=f"extracting facts from {len(chunk_rows)} chunks",
        )
        page_texts = {p.page_index: p.text for p in pages}
        printed_labels = {p.page_index: p.printed_label for p in pages}
        facts = await _run_extraction(
            doc_id=doc_id,
            job_id=job_id,
            chunk_rows=chunk_rows,
            page_texts=page_texts,
            printed_labels=printed_labels,
            profile=profile,
        )

        # ---------------- embed ----------------
        if facts:
            update_job(job_id, stage="embedding", done=0, total=len(facts), message=f"embedding {len(facts)} facts")
            texts = [f["embed_text"] for f in facts]
            vectors = await asyncio.to_thread(embed_texts, texts)
            execute_many(
                "INSERT OR REPLACE INTO fact_embeddings (fact_id, dim, vec) VALUES (?, ?, ?)",
                [(f["id"], int(vectors.shape[1]), to_blob(vectors[i])) for i, f in enumerate(facts)],
            )

        # ---------------- link ----------------
        relation_count = await _run_linking(job_id, facts)

        execute(
            "UPDATE documents SET status = 'ready', completed_at = ? WHERE id = ?",
            (now_iso(), doc_id),
        )
        update_job(
            job_id,
            status="done",
            stage="done",
            message=f"{len(facts)} facts, {relation_count} relationships",
        )
        log.info("ingested %s: %d facts, %d relations", filename, len(facts), relation_count)

    except Exception as exc:
        log.exception("ingest failed for %s", filename)
        execute("UPDATE documents SET status = 'failed', error = ? WHERE id = ?", (str(exc)[:800], doc_id))
        update_job(job_id, status="failed", stage="failed", error=str(exc)[:800], message="ingestion failed")


def register_document(path: Path, filename: str) -> tuple[str, bool]:
    """Insert (or find) the document row. Returns (doc_id, already_existed)."""
    digest = sha256_file(path)
    existing = query("SELECT id FROM documents WHERE sha256 = ?", (digest,))
    if existing:
        return existing[0]["id"], True

    doc_id = new_id("d_")
    execute(
        """INSERT INTO documents (id, filename, title, stored_path, sha256, page_count, byte_size, status, created_at)
           VALUES (?, ?, ?, ?, ?, 0, ?, 'pending', ?)""",
        (doc_id, filename, filename, str(path), digest, path.stat().st_size, now_iso()),
    )
    return doc_id, False


def create_job(doc_id: str) -> str:
    job_id = new_id("j_")
    execute(
        "INSERT INTO jobs (id, doc_id, status, stage, created_at, updated_at) VALUES (?, ?, 'queued', 'queued', ?, ?)",
        (job_id, doc_id, now_iso(), now_iso()),
    )
    return job_id
