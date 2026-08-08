"""HTTP surface tests.

The app is exercised through ASGI without running its lifespan, so these
share the test-database pool opened by conftest rather than connecting to
the development database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.main import app


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http_client:
        yield http_client


# ── health ───────────────────────────────────────────────────────────────────


async def test_health_reports_the_active_provider(client):
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    # conftest pins the offline provider; a test run must never call out.
    assert body["provider"] == "stub"
    assert isinstance(body["auto_apply_threshold"], float)


# ── review queue ─────────────────────────────────────────────────────────────


async def test_queue_is_empty_with_no_proposals(client, seed):
    response = await client.get("/proposed-changes")

    assert response.status_code == 200
    assert response.json() == []


async def test_queue_returns_everything_needed_to_decide(
    client, seed, make_proposal
):
    await make_proposal(seed.sku2_line_id, status="pending", confidence=0.8)

    response = await client.get("/proposed-changes")
    [change] = response.json()

    assert change["po_reference"] == "PO-12"
    assert change["product_sku"] == "SKU-2"
    assert change["field_name"] == "quantity"
    assert change["old_value"] == "200"
    assert change["new_value"] == "500"
    assert change["combined_confidence"] == 0.8
    # The reviewer needs the email that produced this, not just the change.
    assert change["subject"] == "PO-12 update"
    assert change["body_text"] is not None


async def test_queue_can_filter_by_status(client, seed, make_proposal):
    await make_proposal(seed.sku2_line_id, status="pending")
    await make_proposal(seed.sku1_line_id, status="rejected")

    pending = await client.get("/proposed-changes?status=pending")
    rejected = await client.get("/proposed-changes?status=rejected")

    assert len(pending.json()) == 1
    assert len(rejected.json()) == 1


# ── approve ──────────────────────────────────────────────────────────────────


async def test_approve_applies_the_change(client, seed, make_proposal, fetch_one):
    change_id = await make_proposal(
        seed.sku2_line_id, status="pending", new_value="750"
    )

    response = await client.post(
        f"/proposed-changes/{change_id}/approve", json={"reviewed_by": "hanie"}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "applied"}

    line = await fetch_one(
        "SELECT quantity FROM purchase_order_line WHERE id = %s",
        (seed.sku2_line_id,),
    )
    assert float(line["quantity"]) == 750.0

    audit = await fetch_one("SELECT applied_by FROM audit_log")
    assert audit["applied_by"] == "hanie"


async def test_approve_reports_superseded_rather_than_failing(
    client, seed, make_proposal, fetch_one
):
    """A stale proposal is a normal outcome the UI shows, not a 500."""
    change_id = await make_proposal(seed.sku2_line_id, status="pending")

    from backend.db import connection

    async with connection() as conn:
        await conn.execute(
            "UPDATE purchase_order_line SET quantity = 999 WHERE id = %s",
            (seed.sku2_line_id,),
        )

    response = await client.post(
        f"/proposed-changes/{change_id}/approve", json={}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "superseded"}


async def test_approving_a_non_pending_change_is_rejected(
    client, seed, make_proposal
):
    change_id = await make_proposal(seed.sku2_line_id, status="rejected")

    response = await client.post(f"/proposed-changes/{change_id}/approve", json={})

    assert response.status_code == 422
    assert "not pending" in response.json()["detail"]


# ── reject ───────────────────────────────────────────────────────────────────


async def test_reject_records_the_reason(client, seed, make_proposal, fetch_one):
    change_id = await make_proposal(seed.sku2_line_id, status="pending")

    response = await client.post(
        f"/proposed-changes/{change_id}/reject",
        json={"rejection_reason": "wrong_sku", "review_notes": "different product"},
    )

    assert response.status_code == 200
    row = await fetch_one(
        "SELECT status, rejection_reason, review_notes FROM proposed_changes WHERE id = %s",
        (change_id,),
    )
    assert row["status"] == "rejected"
    assert row["rejection_reason"] == "wrong_sku"
    assert row["review_notes"] == "different product"


async def test_reject_requires_a_known_reason(client, seed, make_proposal):
    change_id = await make_proposal(seed.sku2_line_id, status="pending")

    response = await client.post(
        f"/proposed-changes/{change_id}/reject",
        json={"rejection_reason": "i just dont like it"},
    )

    assert response.status_code == 422


async def test_rejecting_leaves_canonical_data_untouched(
    client, seed, make_proposal, fetch_one
):
    change_id = await make_proposal(seed.sku2_line_id, status="pending")

    await client.post(
        f"/proposed-changes/{change_id}/reject",
        json={"rejection_reason": "not_a_po_update"},
    )

    line = await fetch_one(
        "SELECT quantity, version FROM purchase_order_line WHERE id = %s",
        (seed.sku2_line_id,),
    )
    assert float(line["quantity"]) == 200.0
    assert line["version"] == 1
    assert await fetch_one("SELECT * FROM audit_log") is None


# ── ingest ───────────────────────────────────────────────────────────────────

RAW_EMAIL = b"""\
From: Big Supplier <big@supplier.com>
To: ops@acme.test
Subject: PO-12 delivery update
Date: Mon, 12 Jan 2026 09:00:00 +0000
Message-ID: <api-test-1@supplier.com>
Content-Type: text/plain; charset=utf-8

Delivery for SKU-2 on PO-12 has moved to 2026-02-03.
"""


async def test_ingest_stores_the_email(client, seed, fetch_one):
    response = await client.post("/emails?no_pipeline=true", content=RAW_EMAIL)

    assert response.status_code == 200
    body = response.json()
    assert body["is_new"] is True

    email = await fetch_one(
        "SELECT sender, subject, body_text, status FROM emails WHERE id = %s",
        (body["email_id"],),
    )
    assert email["sender"] == "Big Supplier <big@supplier.com>"
    assert email["subject"] == "PO-12 delivery update"
    assert "SKU-2" in email["body_text"]
    assert email["status"] == "ingested"


async def test_ingest_is_idempotent_on_content(client, seed, fetch_all):
    first = await client.post("/emails?no_pipeline=true", content=RAW_EMAIL)
    second = await client.post("/emails?no_pipeline=true", content=RAW_EMAIL)

    assert first.json()["is_new"] is True
    assert second.json()["is_new"] is False
    assert second.json()["email_id"] == first.json()["email_id"]
    assert len(await fetch_all("SELECT id FROM emails")) == 1


async def test_pipeline_endpoint_runs_extract_and_match(client, seed, fetch_one):
    ingested = await client.post("/emails?no_pipeline=true", content=RAW_EMAIL)
    email_id = ingested.json()["email_id"]

    response = await client.post(f"/emails/{email_id}/pipeline")

    assert response.status_code == 200
    body = response.json()
    assert "run_id" in body
    assert body["proposed"] == 0  # no stub fixture matches this subject

    run = await fetch_one(
        "SELECT status, model_version FROM extraction_runs WHERE email_id = %s",
        (email_id,),
    )
    assert run["status"] == "success"
    assert run["model_version"] == "offline-stub"


async def test_pipeline_on_an_unknown_email_is_a_404(client, seed):
    response = await client.post(
        "/emails/00000000-0000-0000-0000-000000000000/pipeline"
    )

    assert response.status_code == 404


# ── monitoring ───────────────────────────────────────────────────────────────


async def test_monitoring_summarises_the_pipeline(client, seed, make_proposal):
    await make_proposal(seed.sku2_line_id, status="pending")

    response = await client.get("/monitoring")

    assert response.status_code == 200
    body = response.json()
    assert body["changes_summary"]["pending"] == 1
    assert body["changes_summary"]["total_applied"] == 0
    assert isinstance(body["status_counts"], list)
    assert isinstance(body["stuck_emails"], list)
