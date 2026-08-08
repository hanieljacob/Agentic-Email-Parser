"""Retry worker: an async polling loop inside the same FastAPI process.

Emails get stuck when the pipeline never started (status='ingested') or when
a previous extraction errored (status='failed'). The worker finds them and
re-runs the pipeline, bounded by WORKER_MAX_RETRIES so a permanently broken
email cannot spin forever.

It runs as a background task started in the app lifespan rather than as a
separate process, so `uvicorn backend.main:app` is the whole backend. Set
WORKER_ENABLED=false to turn it off.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.config import get_settings
from backend.db import connection
from backend.pipeline import run_pipeline

log = logging.getLogger(__name__)

# Ingested but the pipeline never started, or previously failed and still
# under the retry limit. Emails younger than the grace period are left alone
# so the worker never races the request that just created them.
_STUCK_SQL = """
SELECT
  e.id,
  e.status,
  e.sender,
  count(er.id)::integer AS attempt_count
FROM emails e
LEFT JOIN extraction_runs er ON er.email_id = e.id
WHERE (
  e.status = 'ingested'
  AND e.received_at < now() - make_interval(secs => %(grace)s)
  AND NOT EXISTS (
    SELECT 1 FROM extraction_runs er2
    WHERE er2.email_id = e.id
      AND er2.created_at > now() - interval '10 minutes'
  )
) OR (
  e.status = 'failed'
  AND e.received_at < now() - interval '5 minutes'
)
GROUP BY e.id, e.status, e.sender
HAVING count(er.id) < %(max_retries)s
LIMIT 20
"""


async def find_stuck_emails() -> list[dict[str, Any]]:
    settings = get_settings()
    async with connection() as conn:
        cur = await conn.execute(
            _STUCK_SQL,
            {
                "grace": settings.worker_grace_seconds,
                "max_retries": settings.worker_max_retries,
            },
        )
        return await cur.fetchall()


async def tick() -> int:
    """One pass. Returns how many emails were successfully re-triggered."""
    stuck = await find_stuck_emails()
    if not stuck:
        return 0

    log.info("retrying %d stuck email(s)", len(stuck))
    retriggered = 0
    for email in stuck:
        try:
            await run_pipeline(str(email["id"]))
            retriggered += 1
            log.info(
                "retriggered %s status=%s attempts=%s from=%s",
                email["id"],
                email["status"],
                email["attempt_count"],
                email["sender"],
            )
        except Exception as err:
            log.error("failed to retrigger %s: %s", email["id"], err)
    return retriggered


async def run_worker() -> None:
    """Poll forever. Cancelled by the app lifespan on shutdown."""
    settings = get_settings()
    log.info(
        "worker starting interval=%ss grace=%ss max_retries=%s",
        settings.worker_interval_seconds,
        settings.worker_grace_seconds,
        settings.worker_max_retries,
    )
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            log.info("worker stopping")
            raise
        except Exception as err:
            log.error("worker tick error: %s", err)
        await asyncio.sleep(settings.worker_interval_seconds)
