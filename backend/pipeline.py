"""Extract → match, as one call.

Every entry point that processes an email — HTTP ingest, the compose page,
the retry worker, the fixture loader — goes through `run_pipeline` so there
is exactly one definition of what "process this email" means.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.db import connection
from backend.extract import extract
from backend.llm import ExtractionProvider
from backend.match import match

log = logging.getLogger(__name__)


async def run_pipeline(
    email_id: str,
    provider: ExtractionProvider | None = None,
) -> dict[str, Any]:
    """Extract then match one email. Returns the run id and match summary."""
    run_id = await extract(email_id, provider=provider)
    summary = await match(run_id)

    async with connection() as conn:
        cur = await conn.execute(
            """
            SELECT count(*)::integer AS pending
            FROM proposed_changes
            WHERE extraction_run_id = %s AND status = 'pending'
            """,
            (run_id,),
        )
        row = await cur.fetchone()

    return {"run_id": run_id, "pending": row["pending"] if row else 0, **summary}


async def run_pipeline_safely(email_id: str) -> None:
    """Fire-and-forget wrapper for background tasks. Never raises."""
    try:
        await run_pipeline(email_id)
    except Exception as err:
        log.error("pipeline failed for %s: %s", email_id, err)
