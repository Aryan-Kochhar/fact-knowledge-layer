-- Fact Knowledge Layer storage.
--
-- Design notes:
--  * SQLite because the whole system is single-node and the working set is
--    small (thousands of facts). It gives transactions, joins and a durable
--    single-file artifact with zero operational cost. See README trade-offs.
--  * Embeddings live in a BLOB column and are brute-force scanned in numpy.
--    At this scale (<1e5 vectors) that is sub-millisecond and avoids pulling in
--    a vector database.
--  * Everything is keyed by document so an ingest is fully incremental and a
--    document can be deleted without touching other documents' facts.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
    id            TEXT PRIMARY KEY,
    filename      TEXT NOT NULL,
    title         TEXT,
    stored_path   TEXT NOT NULL,
    sha256        TEXT NOT NULL UNIQUE,
    page_count    INTEGER NOT NULL DEFAULT 0,
    byte_size     INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'pending',
    error         TEXT,
    -- The context inferred once from the document's opening pages: entity,
    -- reporting period, currency, scale, accounting scope. Persisted rather than
    -- used and discarded, because facts inherit their period from it. Without it
    -- stored, "why does this fact say FY2025 when its page never does?" has no
    -- answer, and deterministic fields cannot be re-derived offline.
    profile       TEXT,
    created_at    TEXT NOT NULL,
    completed_at  TEXT
);

CREATE TABLE IF NOT EXISTS pages (
    doc_id        TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_index    INTEGER NOT NULL,          -- 1-based position inside the PDF file
    printed_label TEXT,                      -- page number printed on the page, if found
    text          TEXT NOT NULL,
    char_count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (doc_id, page_index)
);

CREATE TABLE IF NOT EXISTS chunks (
    id          TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal     INTEGER NOT NULL,
    page_start  INTEGER NOT NULL,
    page_end    INTEGER NOT NULL,
    char_count  INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'pending',
    error       TEXT,
    fact_count  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);

CREATE TABLE IF NOT EXISTS facts (
    id             TEXT PRIMARY KEY,
    doc_id         TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_id       TEXT REFERENCES chunks(id) ON DELETE SET NULL,

    -- Model-authored fields. Deliberately loose: the model names the metric in
    -- the document's own language rather than mapping onto a fixed schema.
    subject        TEXT NOT NULL,
    predicate      TEXT NOT NULL,
    value_raw      TEXT NOT NULL,
    unit_raw       TEXT,
    time_scope_raw TEXT,
    qualifiers     TEXT NOT NULL DEFAULT '[]',   -- JSON array of scope tags
    fact_type      TEXT,                          -- numeric | categorical | event | ...
    confidence     REAL NOT NULL DEFAULT 0.5,

    -- Deterministically derived comparison keys (normalize.py). These are what
    -- the reconciliation logic actually reasons over.
    value_num      REAL,       -- magnitude-scaled numeric value, base units
    value_unit     TEXT,       -- canonical unit token: INR, USD, percent, count, ...
    value_dim      TEXT,       -- dimension: currency | ratio | count | mass | ...
    period_key     TEXT,       -- FY2024, Q4FY2024, CY2023, 2024-06, ...
    period_start   TEXT,
    period_end     TEXT,
    period_basis   TEXT,       -- actual | estimate | projection | target | unknown
    metric_key     TEXT,       -- blocking key: normalized subject+predicate
    claim_text     TEXT NOT NULL,  -- canonical rendering; what gets embedded

    -- Evidence
    quote          TEXT NOT NULL,
    page_index     INTEGER,
    printed_page   TEXT,
    verification   TEXT NOT NULL DEFAULT 'unverified',  -- verified | relocated | unverified
    verify_score   REAL NOT NULL DEFAULT 0.0,
    quote_start    INTEGER,     -- char offset of the matched quote inside the page
    quote_end      INTEGER,

    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_doc ON facts(doc_id);
CREATE INDEX IF NOT EXISTS idx_facts_metric ON facts(metric_key);
CREATE INDEX IF NOT EXISTS idx_facts_period ON facts(period_key);

CREATE TABLE IF NOT EXISTS fact_embeddings (
    fact_id TEXT PRIMARY KEY REFERENCES facts(id) ON DELETE CASCADE,
    dim     INTEGER NOT NULL,
    vec     BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS relations (
    id           TEXT PRIMARY KEY,
    fact_a       TEXT NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
    fact_b       TEXT NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
    cross_doc    INTEGER NOT NULL DEFAULT 0,
    relation     TEXT NOT NULL,   -- corroborates | contradicts | reconcilable_context | unrelated
    reasoning    TEXT NOT NULL,
    reconciliation TEXT,          -- what makes the apparent conflict go away
    dimension    TEXT,            -- time | unit | scope | basis | none
    confidence   REAL NOT NULL DEFAULT 0.5,   -- after any flagging penalty
    raw_confidence REAL,                      -- as the model reported it
    similarity   REAL NOT NULL DEFAULT 0.0,
    decided_by   TEXT NOT NULL DEFAULT 'llm',  -- llm | llm-flagged | rule
    created_at   TEXT NOT NULL,
    UNIQUE (fact_a, fact_b)
);
CREATE INDEX IF NOT EXISTS idx_relations_type ON relations(relation);
CREATE INDEX IF NOT EXISTS idx_relations_a ON relations(fact_a);
CREATE INDEX IF NOT EXISTS idx_relations_b ON relations(fact_b);

-- Every extraction that we rejected or repaired, kept on purpose so failure
-- modes are inspectable in the UI instead of silently swallowed.
CREATE TABLE IF NOT EXISTS issues (
    id         TEXT PRIMARY KEY,
    doc_id     TEXT REFERENCES documents(id) ON DELETE CASCADE,
    chunk_id   TEXT,
    kind       TEXT NOT NULL,      -- quote_not_found | quote_relocated | bad_json | ...
    severity   TEXT NOT NULL DEFAULT 'warning',
    detail     TEXT NOT NULL,
    payload    TEXT,               -- JSON: the offending model output
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_issues_doc ON issues(doc_id);

CREATE TABLE IF NOT EXISTS jobs (
    id         TEXT PRIMARY KEY,
    doc_id     TEXT,
    status     TEXT NOT NULL,      -- queued | running | done | failed
    stage      TEXT NOT NULL DEFAULT 'queued',
    done       INTEGER NOT NULL DEFAULT 0,
    total      INTEGER NOT NULL DEFAULT 0,
    message    TEXT,
    error      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Lightweight observability for the key pool / quota behaviour.
CREATE TABLE IF NOT EXISTS llm_calls (
    id          TEXT PRIMARY KEY,
    purpose     TEXT NOT NULL,
    model       TEXT NOT NULL,
    key_index   INTEGER,
    status      TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 1,
    latency_ms  INTEGER NOT NULL DEFAULT 0,
    in_chars    INTEGER NOT NULL DEFAULT 0,
    out_chars   INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    created_at  TEXT NOT NULL
);
