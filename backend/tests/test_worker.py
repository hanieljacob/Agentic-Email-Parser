"""The retry worker's discovery query.

The worker exists to catch emails the pipeline never finished with. What it
must not do is re-process healthy emails, race the request that just created
one, or retry a permanently broken email forever.
"""

from __future__ import annotations

import json
import uuid

from backend.config import get_settings
from backend.db import connection
from backend.worker import find_stuck_emails, tick


async def _email(
    status: str,
    *,
    age_minutes: int = 0,
    runs: int = 0,
    run_age_minutes: int = 0,
    subject: str = "PO-12 update",
) -> str:
    """Insert an email with a chosen age, status and extraction history."""
    async with connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO emails
              (message_id, sender, subject, received_at, body_text, content_hash, status)
            VALUES (%s, 'Big Supplier <big@supplier.com>', %s,
                    now() - make_interval(mins => %s), 'body', %s, %s)
            RETURNING id
            """,
            (f"<{uuid.uuid4()}@t.local>", subject, age_minutes, uuid.uuid4().hex, status),
        )
        email_id = str((await cur.fetchone())["id"])

        for _ in range(runs):
            await conn.execute(
                """
                INSERT INTO extraction_runs
                  (email_id, model_version, llm_output, status, created_at)
                VALUES (%s, 'test', %s, 'error',
                        now() - make_interval(mins => %s))
                """,
                (email_id, json.dumps({}), run_age_minutes),
            )
    return email_id


def _ids(rows) -> set[str]:
    return {str(r["id"]) for r in rows}


# ── what counts as stuck ─────────────────────────────────────────────────────


async def test_an_old_ingested_email_is_stuck(seed):
    email_id = await _email("ingested", age_minutes=30)

    assert email_id in _ids(await find_stuck_emails())


async def test_a_fresh_ingested_email_is_left_alone(seed):
    """Inside the grace window the pipeline may still be running."""
    email_id = await _email("ingested", age_minutes=0)

    assert email_id not in _ids(await find_stuck_emails())


async def test_an_old_failed_email_is_retried(seed):
    email_id = await _email("failed", age_minutes=30, runs=1, run_age_minutes=30)

    assert email_id in _ids(await find_stuck_emails())


async def test_a_recently_failed_email_is_left_alone(seed):
    email_id = await _email("failed", age_minutes=1, runs=1, run_age_minutes=1)

    assert email_id not in _ids(await find_stuck_emails())


async def test_an_ingested_email_with_a_recent_run_is_not_stuck(seed):
    """A run in the last ten minutes means something is already working on it."""
    email_id = await _email("ingested", age_minutes=30, runs=1, run_age_minutes=1)

    assert email_id not in _ids(await find_stuck_emails())


async def test_healthy_statuses_are_never_picked_up(seed):
    ids = [
        await _email(status, age_minutes=120)
        for status in ("matched", "extracted", "needs_review", "skipped")
    ]

    stuck = _ids(await find_stuck_emails())
    assert stuck.isdisjoint(ids)


async def test_the_retry_limit_is_respected(seed):
    """Past WORKER_MAX_RETRIES a broken email stops consuming attempts."""
    limit = get_settings().worker_max_retries
    at_limit = await _email(
        "failed", age_minutes=30, runs=limit, run_age_minutes=30
    )
    under_limit = await _email(
        "failed", age_minutes=30, runs=limit - 1, run_age_minutes=30
    )

    stuck = _ids(await find_stuck_emails())
    assert at_limit not in stuck
    assert under_limit in stuck


async def test_discovery_is_batched(seed):
    for _ in range(25):
        await _email("ingested", age_minutes=30)

    assert len(await find_stuck_emails()) == 20


# ── a tick ───────────────────────────────────────────────────────────────────


async def test_a_tick_reprocesses_a_stuck_email(seed, fetch_one, monkeypatch):
    from backend.llm import StubProvider

    email_id = await _email("ingested", age_minutes=30, subject="PO-12 update")

    # The worker builds its own provider deep in extract(); pin the offline
    # one explicitly rather than relying on conftest's env pin.
    monkeypatch.setattr("backend.extract.build_provider", lambda: StubProvider())

    processed = await tick()

    assert processed == 1
    email = await fetch_one("SELECT status FROM emails WHERE id = %s", (email_id,))
    assert email["status"] != "ingested"


async def test_a_tick_with_nothing_stuck_does_no_work(seed):
    await _email("matched", age_minutes=120)

    assert await tick() == 0


async def test_one_failing_email_does_not_stop_the_batch(seed, monkeypatch):
    """A single bad email must not strand the rest of the queue."""
    calls: list[str] = []

    async def flaky(email_id: str, provider=None):
        calls.append(email_id)
        if len(calls) == 1:
            raise RuntimeError("first one explodes")
        return {"run_id": "x", "pending": 0, "proposed": 0, "auto_applied": 0}

    monkeypatch.setattr("backend.worker.run_pipeline", flaky)
    await _email("ingested", age_minutes=30)
    await _email("ingested", age_minutes=31)
    await _email("ingested", age_minutes=32)

    processed = await tick()

    assert len(calls) == 3
    assert processed == 2
