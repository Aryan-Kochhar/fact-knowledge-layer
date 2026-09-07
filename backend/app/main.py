"""FastAPI entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
    init_db()
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


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "fact-knowledge-layer", "docs": "/docs", "api": "/api/health"}
