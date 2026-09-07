"""SQLite access layer.

One connection per operation, WAL mode, generous busy timeout. That is enough
concurrency control for a single-node app where writes are short and batched,
and it keeps the rest of the codebase free of session/ORM ceremony.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import settings

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
_init_lock = threading.Lock()
_initialised = False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:16]
    return f"{prefix}{raw}" if prefix else raw


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db() -> None:
    global _initialised
    with _init_lock:
        if _initialised:
            return
        settings.ensure_dirs()
        conn = _connect()
        try:
            conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
            _migrate(conn)
            conn.commit()
        finally:
            conn.close()
        _initialised = True


# Columns added after the first release. `CREATE TABLE IF NOT EXISTS` will not
# add a column to a table that already exists, so new ones are applied here.
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("documents", "profile", "TEXT"),
    ("relations", "raw_confidence", "REAL"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in _ADDED_COLUMNS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    if not _initialised:
        init_db()
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple | dict = ()) -> None:
    with get_conn() as conn:
        conn.execute(sql, params)


def execute_many(sql: str, seq: list[tuple]) -> None:
    if not seq:
        return
    with get_conn() as conn:
        conn.executemany(sql, seq)


# --------------------------------------------------------------------------
# small typed helpers used across the pipeline
# --------------------------------------------------------------------------

def record_issue(
    *,
    doc_id: str | None,
    chunk_id: str | None,
    kind: str,
    detail: str,
    severity: str = "warning",
    payload: Any = None,
) -> str:
    issue_id = new_id("iss_")
    execute(
        """INSERT INTO issues (id, doc_id, chunk_id, kind, severity, detail, payload, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            issue_id,
            doc_id,
            chunk_id,
            kind,
            severity,
            detail,
            json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            now_iso(),
        ),
    )
    return issue_id


def record_llm_call(
    *,
    purpose: str,
    model: str,
    key_index: int | None,
    status: str,
    attempts: int,
    latency_ms: int,
    in_chars: int,
    out_chars: int,
    error: str | None = None,
) -> None:
    execute(
        """INSERT INTO llm_calls
           (id, purpose, model, key_index, status, attempts, latency_ms, in_chars, out_chars, error, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            new_id("call_"),
            purpose,
            model,
            key_index,
            status,
            attempts,
            latency_ms,
            in_chars,
            out_chars,
            error,
            now_iso(),
        ),
    )


def calls_in_last_day() -> int:
    """LLM calls made in the trailing 24 hours - drives the free-tier budget guard.

    The cutoff is built in Python so it is byte-comparable with how `now_iso()`
    writes timestamps; SQLite's own `datetime('now')` uses a different separator
    and no offset, which would make the string comparison subtly wrong.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    row = query_one("SELECT COUNT(*) AS n FROM llm_calls WHERE created_at >= ?", (cutoff,))
    return int(row["n"]) if row else 0


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))
