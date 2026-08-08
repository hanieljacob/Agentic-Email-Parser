"""Test fixtures.

Tests run against a real PostgreSQL database — the point of most of them is
the transactional behaviour (optimistic locking, audit rows, status
transitions), which a mock would not exercise. The database is created and
migrated once per session, then reset between tests.

Override the target with TEST_DATABASE_URL; the default is a dedicated
`email_parser_test` database so a test run can never touch dev data.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest
import pytest_asyncio
from psycopg.rows import dict_row

from backend.config import PROJECT_ROOT
from backend.db import close_pool, connection, open_pool

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://localhost/email_parser_test"
)
MAINTENANCE_URL = os.environ.get(
    "TEST_MAINTENANCE_DATABASE_URL", "postgresql://localhost/postgres"
)
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"

# Everything the pipeline writes; wiped between tests. Canonical tables are
# reseeded by the `seed` fixture, so they are truncated here too.
_RESET_SQL = """
TRUNCATE audit_log, proposed_changes, extraction_runs, email_attachments, emails,
         supplier_corrections, supplier_email_aliases, supplier_product,
         purchase_order_line, purchase_order, product, supplier
RESTART IDENTITY CASCADE
"""


def _database_name(url: str) -> str:
    return url.rsplit("/", 1)[-1].split("?")[0]


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    """Create the test database if needed and apply every migration in order."""
    name = _database_name(TEST_DATABASE_URL)

    with psycopg.connect(MAINTENANCE_URL, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
        ).fetchone()
        if exists:
            # Drop and recreate so a schema change in a migration can never
            # leave a stale test database behind.
            conn.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{name}"')

    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]


@pytest_asyncio.fixture(autouse=True)
async def pool() -> Any:
    """Open a pool bound to this test's event loop, and reset the data."""
    await open_pool(TEST_DATABASE_URL)
    async with connection() as conn:
        await conn.execute(_RESET_SQL)
    yield
    await close_pool()


# ── canonical seed data ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Seed:
    """Ids of the deterministic canonical dataset the tests match against."""

    supplier_id: str
    po12_id: str
    sku1_line_id: str
    sku2_line_id: str
    #: product SKU-1-3 — reachable only by the supplier's own code "SKU13"
    alias_line_id: str


@pytest_asyncio.fixture
async def seed() -> Seed:
    """A miniature version of backend/data/db.xlsx.

    One supplier with one purchase order carrying three lines, one of which
    is only reachable through the supplier's own product code — that is the
    line that exercises the 0.9 alias-match path.
    """
    async with connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO supplier (legacy_id, name, email)
            VALUES (1, 'Big Supplier', 'big@supplier.com') RETURNING id
            """
        )
        supplier_id = (await cur.fetchone())["id"]

        await conn.execute(
            "INSERT INTO supplier_email_aliases (supplier_id, email_address) VALUES (%s, %s)",
            (supplier_id, "big@supplier.com"),
        )

        products: dict[str, str] = {}
        for legacy_id, sku, title in [
            (1, "SKU-1", "PRODUCT ONE | GLOBAL VERSION"),
            (2, "SKU-2", "PRODUCT TWO with Vitamin A, B, C"),
            (5, "SKU-1-3", "PRODUCT ONE | GLOBAL VERSION updated v3"),
        ]:
            cur = await conn.execute(
                "INSERT INTO product (legacy_id, sku, title) VALUES (%s,%s,%s) RETURNING id",
                (legacy_id, sku, title),
            )
            products[sku] = (await cur.fetchone())["id"]

        # The supplier calls SKU-1-3 "SKU13" in their own emails.
        await conn.execute(
            """
            INSERT INTO supplier_product (supplier_id, product_id, supplier_sku, price_per_unit)
            VALUES (%s, %s, 'SKU13', 2.0)
            """,
            (supplier_id, products["SKU-1-3"]),
        )

        cur = await conn.execute(
            """
            INSERT INTO purchase_order (legacy_id, reference_num, supplier_id, delivery_date)
            VALUES (1, 'PO-12', %s, '2026-01-15') RETURNING id
            """,
            (supplier_id,),
        )
        po12_id = (await cur.fetchone())["id"]

        lines: dict[str, str] = {}
        for sku, quantity in [("SKU-1", 10000), ("SKU-2", 200), ("SKU-1-3", 15000)]:
            cur = await conn.execute(
                """
                INSERT INTO purchase_order_line
                  (purchase_order_id, product_id, quantity, delivery_date)
                VALUES (%s, %s, %s, '2026-01-15') RETURNING id
                """,
                (po12_id, products[sku], quantity),
            )
            lines[sku] = (await cur.fetchone())["id"]

    return Seed(
        supplier_id=str(supplier_id),
        po12_id=str(po12_id),
        sku1_line_id=str(lines["SKU-1"]),
        sku2_line_id=str(lines["SKU-2"]),
        alias_line_id=str(lines["SKU-1-3"]),
    )


# ── helpers ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def make_email():
    """Insert an email row and return its id."""

    async def _make(
        subject: str = "PO-12 update",
        sender: str = "Big Supplier <big@supplier.com>",
        body: str = "Please see the update below.",
    ) -> str:
        async with connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO emails
                  (message_id, sender, subject, received_at, body_text, content_hash, status)
                VALUES (%s, %s, %s, now(), %s, %s, 'ingested')
                RETURNING id
                """,
                (
                    f"<{uuid.uuid4()}@test.local>",
                    sender,
                    subject,
                    body,
                    uuid.uuid4().hex,
                ),
            )
            return str((await cur.fetchone())["id"])

    return _make


@pytest_asyncio.fixture
async def make_run(make_email):
    """Insert an email plus a successful extraction_run carrying `llm_output`."""

    async def _make(
        llm_output: dict[str, Any],
        subject: str = "PO-12 update",
        sender: str = "Big Supplier <big@supplier.com>",
    ) -> tuple[str, str]:
        email_id = await make_email(subject=subject, sender=sender)
        async with connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO extraction_runs (email_id, model_version, llm_output, status)
                VALUES (%s, 'test-model', %s, 'success')
                RETURNING id
                """,
                (email_id, json.dumps(llm_output)),
            )
            return email_id, str((await cur.fetchone())["id"])

    return _make


@pytest_asyncio.fixture
async def make_proposal(make_run):
    """Stage a proposed_changes row directly, bypassing extract and match."""

    async def _make(
        target_record_id: str,
        *,
        field_name: str = "quantity",
        old_value: str | None = "200",
        new_value: str = "500",
        target_record_version: int | None = None,
        status: str = "approved",
        confidence: float = 1.0,
    ) -> str:
        _, run_id = await make_run({"po_updates": [], "unmatched_mentions": []})
        async with connection() as conn:
            if target_record_version is None:
                cur = await conn.execute(
                    "SELECT version FROM purchase_order_line WHERE id = %s",
                    (target_record_id,),
                )
                target_record_version = (await cur.fetchone())["version"]

            cur = await conn.execute(
                """
                INSERT INTO proposed_changes
                  (email_id, extraction_run_id,
                   target_table, target_record_id, target_record_version,
                   field_name, old_value, new_value, evidence_text,
                   extraction_confidence, match_confidence, combined_confidence, status)
                SELECT er.email_id, er.id,
                       'purchase_order_line', %s, %s,
                       %s, %s, %s, 'test evidence',
                       %s, 1.0, %s, %s
                FROM extraction_runs er WHERE er.id = %s
                RETURNING id
                """,
                (
                    target_record_id,
                    target_record_version,
                    field_name,
                    old_value,
                    new_value,
                    confidence,
                    confidence,
                    status,
                    run_id,
                ),
            )
            return str((await cur.fetchone())["id"])

    return _make


@pytest_asyncio.fixture
async def fetch_one():
    """Run a query and return the single row (or None)."""

    async def _fetch(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        async with connection() as conn:
            cur = await conn.execute(query, params)
            return await cur.fetchone()

    return _fetch


@pytest_asyncio.fixture
async def fetch_all():
    async def _fetch(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        async with connection() as conn:
            cur = await conn.execute(query, params)
            return await cur.fetchall()

    return _fetch


@pytest.fixture
def raw_connect():
    """A connection outside the pool, for simulating another writer."""

    def _connect() -> psycopg.Connection:
        return psycopg.connect(TEST_DATABASE_URL, row_factory=dict_row)

    return _connect
