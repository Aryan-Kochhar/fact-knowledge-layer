"""FastAPI entrypoint."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import settings
from .db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)-14s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fkl")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    if settings.seed_if_empty():
        log.info("seeded %s from %s", settings.db_path, settings.seed_db)
    init_db()
    if settings.demo_mode:
        log.info("DEMO_MODE is on: uploads are disabled, existing results are served read-only")
    if settings.has_keys:
        log.info("Gemini key pool: %d key(s), %d rpm each", len(settings.gemini_keys), settings.per_key_rpm)
    else:
        log.warning("No Gemini API keys configured - uploads will be rejected. Set GEMINI_API_KEYS in backend/.env")

    # Warm the encoder in the background so the first ingest does not pay the
    # model-load cost inside a request.
    import asyncio

    async def warm() -> None:
        from .pipeline.embeddings import get_model

        try:
            await asyncio.to_thread(get_model)
            log.info("embedding model ready")
        except Exception as exc:
            log.warning("embedding model failed to load: %s", exc)

    task = asyncio.create_task(warm())
    yield
    task.cancel()

    from .llm.gemini import aclose

    await aclose()


app = FastAPI(
    title="Fact Knowledge Layer",
    description="Extracts evidence-linked facts from PDFs and reconciles them across documents.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # Local-only tool: the Vite dev server and any localhost port the UI is served from.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


# --------------------------------------------------------------------------
# Optional single-service mode
#
# In development the Vite dev server proxies /api to this process. In a
# container there is only one port to expose, so if a built frontend is present
# we serve it from here too and the whole app becomes one process behind one
# URL. Falls back to the JSON root when there is no build, so local development
# is unaffected.
# --------------------------------------------------------------------------
_dist = Path(os.getenv("FRONTEND_DIST") or (Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"))

if (_dist / "index.html").is_file():
    log.info("serving built frontend from %s", _dist)

    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        """Serve static files, falling back to index.html for client-side routes."""
        candidate = (_dist / full_path).resolve()
        # Keep the fallback from being turned into a path-traversal read.
        if full_path and candidate.is_file() and _dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(_dist / "index.html")

else:

    @app.get("/")
    def root() -> dict[str, str]:
        return {"service": "fact-knowledge-layer", "docs": "/docs", "api": "/api/health"}
