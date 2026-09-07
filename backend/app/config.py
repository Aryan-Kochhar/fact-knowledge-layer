"""Runtime configuration.

Everything tunable lives here so the pipeline has no magic numbers buried in it.
Values come from environment variables (loaded from backend/.env) with sane
defaults, so the app runs with zero configuration except the API keys.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent

load_dotenv(BACKEND_DIR / ".env")


def _split_keys(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = [p.strip() for p in raw.replace("\n", ",").split(",")]
    return [p for p in parts if p]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # --- storage ---
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR") or PROJECT_DIR / "data"))
    # On first boot, copy this database in if the data directory is empty. Lets a
    # container ship with the corpus already ingested so the demo works instantly
    # and without spending anyone's quota.
    seed_db: Path | None = field(
        default_factory=lambda: Path(os.getenv("SEED_DB")) if os.getenv("SEED_DB") else None
    )

    # --- deployment guards ---
    # A public URL means strangers can spend the owner's free-tier quota. These
    # bound the damage; DAILY_CALL_BUDGET is the backstop behind them.
    max_upload_mb: int = _env_int("MAX_UPLOAD_MB", 25)
    max_pages_per_upload: int = _env_int("MAX_PAGES_PER_UPLOAD", 0)  # 0 = unlimited
    # Read-only: serve everything already ingested, refuse new uploads.
    demo_mode: bool = _env_bool("DEMO_MODE", False)

    # --- gemini ---
    # GEMINI_API_KEYS is a comma (or newline) separated pool. GEMINI_API_KEY is
    # accepted as a single-key fallback so the app works with one key too.
    gemini_keys: list[str] = field(
        default_factory=lambda: _split_keys(os.getenv("GEMINI_API_KEYS")) or _split_keys(os.getenv("GEMINI_API_KEY"))
    )
    # Two models, matched to two very different jobs.
    #
    # Extraction is high-volume and mechanical: read a page, copy figures and
    # quotes out of it. Measured on this corpus, a non-thinking "lite" model does
    # it in ~1.5s per call versus ~7-19s for a thinking model, with no measurable
    # loss on the copying task - and extraction is ~90% of all calls.
    #
    # Judgment is low-volume and genuinely hard: convert units, compare fiscal
    # calendars, weigh scope qualifiers, and decide whether two numbers can both
    # be true. That is exactly where thinking tokens earn their cost, and there
    # are ~10x fewer of those calls, so the extra latency is affordable.
    # Model choice was measured, not assumed (scripts/check_keys.py and a burst
    # test): on this free tier the newest flash model returned 429/503 on 5 of 5
    # concurrent calls, while gemini-3.5-flash answered 5 of 5 and gave the same
    # verdict every time on a basis-difference case that the lite model got wrong
    # once in five. Availability and consistency beat recency here.
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    gemini_judge_model: str = os.getenv("GEMINI_JUDGE_MODEL", "gemini-3.1-flash-lite")
    # Reserved for the pairs where the verdict is hardest and most consequential:
    # same period, same units, values that disagree - the contradiction
    # signature. A thinking model is measurably better at those (it was the only
    # candidate to answer a basis-difference case the same way five times out of
    # five), but its free-tier daily allowance is small enough that it cannot
    # judge a whole corpus. Spending it only where it changes the answer is the
    # compromise; if it is exhausted, the circuit breaker routes straight past it.
    gemini_escalation_model: str = os.getenv("GEMINI_ESCALATION_MODEL", "gemini-3.5-flash")
    gemini_fallback_model: str = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.1-flash-lite")
    gemini_timeout_s: float = _env_float("GEMINI_TIMEOUT_S", 120.0)
    # Free tier is ~15 requests/min/key for flash. We stay under it per key.
    per_key_rpm: int = _env_int("GEMINI_PER_KEY_RPM", 10)
    max_attempts_per_call: int = _env_int("GEMINI_MAX_ATTEMPTS", 6)
    # How many extraction calls run at once. Bounded by pool size at runtime.
    extraction_concurrency: int = _env_int("EXTRACTION_CONCURRENCY", 3)
    # Free tier also caps requests *per day*. This is a soft guard so a large
    # ingest cannot silently burn a whole day's quota; ingestion refuses to start
    # when the last 24h of calls already exceed it. Set 0 to disable.
    daily_call_budget: int = _env_int("DAILY_CALL_BUDGET", 200)

    # --- chunking ---
    # Target characters of page text per LLM extraction call.
    #
    # This is the main quota dial. The model's context window is far larger than
    # anything we send, so the real trade-off is recall, not capacity: a large
    # chunk costs fewer calls but asks the model to notice every fact across more
    # text, and a single failure loses more work. 15k characters (~4k tokens) is
    # where the starter corpus fits a dense statistical table plus its
    # surrounding prose without approaching the output-token cap. Citation
    # precision is unaffected either way - page markers travel inside the chunk
    # and every quote is verified against real page text afterwards.
    chunk_target_chars: int = _env_int("CHUNK_TARGET_CHARS", 15000)
    chunk_max_chars: int = _env_int("CHUNK_MAX_CHARS", 20000)
    # Cap on facts requested per chunk. Bounded by the output token limit: each
    # fact record costs roughly 150-250 output tokens.
    max_facts_per_chunk: int = _env_int("MAX_FACTS_PER_CHUNK", 40)
    # Pages with less text than this get merged with neighbours (slide decks,
    # section dividers) instead of burning a whole call each.
    page_min_chars: int = _env_int("PAGE_MIN_CHARS", 400)
    extract_tables: bool = _env_bool("EXTRACT_TABLES", True)
    max_tables_per_page: int = _env_int("MAX_TABLES_PER_PAGE", 6)

    # --- embeddings ---
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

    # --- linking ---
    # Cosine floor for a pair to be worth a judgment. Tuned on the starter set:
    # below ~0.55 the pairs are almost always unrelated metrics.
    similarity_threshold: float = _env_float("SIMILARITY_THRESHOLD", 0.58)
    # Max candidate partners considered per new fact.
    max_candidates_per_fact: int = _env_int("MAX_CANDIDATES_PER_FACT", 5)
    # Pairs judged per LLM call. Batching is what keeps judgment affordable:
    # at 12 per call, a thousand candidate pairs cost 84 calls instead of 1000.
    judge_batch_size: int = _env_int("JUDGE_BATCH_SIZE", 12)
    # Relative difference below which two numbers count as "the same number".
    numeric_tolerance: float = _env_float("NUMERIC_TOLERANCE", 0.005)
    # Hard ceiling on pairs sent for judgment per ingested document.
    #
    # Candidate pairs grow roughly quadratically with corpus size: three
    # 100-page reports produce a few thousand facts, and top-5 nearest
    # neighbours over those is >10,000 pairs - more judgment calls than a free
    # tier grants in a day. Rather than truncate arbitrarily, candidates are
    # ranked by how likely they are to yield an *interesting* verdict (see
    # linking.priority) and the budget is spent from the top down.
    max_pairs_per_ingest: int = _env_int("MAX_PAIRS_PER_INGEST", 420)

    # --- evidence verification ---
    # Minimum similarity between an LLM-supplied quote and real page text for the
    # quote to count as verified. See pipeline/verify.py.
    quote_match_threshold: float = _env_float("QUOTE_MATCH_THRESHOLD", 0.82)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "facts.db"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def has_keys(self) -> bool:
        return bool(self.gemini_keys)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    def seed_if_empty(self) -> bool:
        """Copy the seed database in when there is no database yet."""
        if not self.seed_db or self.db_path.exists() or not self.seed_db.is_file():
            return False
        import shutil

        self.ensure_dirs()
        shutil.copy2(self.seed_db, self.db_path)
        return True


settings = Settings()
settings.ensure_dirs()
