"""psycopg 3 connection pool.

One pool for the whole process, opened on FastAPI startup and closed on
shutdown. Every query in the backend goes through `connection()`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from backend.config import get_settings

_pool: AsyncConnectionPool | None = None


async def open_pool(conninfo: str | None = None) -> AsyncConnectionPool:
    """Open the process-wide pool. Idempotent."""
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            conninfo or get_settings().database_url,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=False,
        )
        await _pool.open(wait=True)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("connection pool is not open — call open_pool() first")
    return _pool


@asynccontextmanager
async def connection() -> AsyncIterator[AsyncConnection]:
    """Check a connection out of the pool.

    psycopg opens a transaction implicitly on first execute and commits it
    when the block exits cleanly, so callers that need explicit transaction
    boundaries should use `async with conn.transaction():` inside this block.
    """
    async with get_pool().connection() as conn:
        yield conn
