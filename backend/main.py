"""The backend. One FastAPI app, one uvicorn process.

    uvicorn backend.main:app --reload

Replaces what used to be four processes: the Python ingest service on 8000,
the TypeScript extract server on 8001, the Express API on 8002, and the
standalone retry worker. The worker now runs as a background task inside
this app's lifespan.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import get_settings
from backend.db import close_pool, open_pool
from backend.routers import emails, monitoring, proposed_changes
from backend.worker import run_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    await open_pool()
    log.info("extraction provider: %s", settings.resolved_provider)

    worker_task: asyncio.Task[None] | None = None
    if settings.worker_enabled:
        worker_task = asyncio.create_task(run_worker())

    try:
        yield
    finally:
        if worker_task is not None:
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task
        await close_pool()


app = FastAPI(
    title="Agentic Email Parser",
    description=(
        "Ingests supplier emails, extracts purchase order updates with an "
        "LLM, matches them to canonical records, and either auto-applies "
        "high-confidence changes or routes them to a human review queue."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(emails.router)
app.include_router(proposed_changes.router)
app.include_router(monitoring.router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, object]:
    settings = get_settings()
    return {
        "ok": True,
        "provider": settings.resolved_provider,
        "model": settings.model_name,
        "auto_apply_threshold": settings.auto_apply_threshold,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=get_settings().api_port,
        reload=True,
    )
