"""Writeback under concurrency.

The apply path is the only writer to canonical data, and it has to hold two
guarantees under a concurrent update: never overwrite a row that has moved
since the change was proposed, and never write without an audit row.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.db import connection
from backend.writeback import (
    NotApplicableError,
    ProposedChangeNotFoundError,
    apply_proposed_change,
)


# ── the happy path ───────────────────────────────────────────────────────────


async def test_apply_writes_the_value_and_bumps_the_version(
    seed, make_proposal, fetch_one
):
    pc_id = await make_proposal(seed.sku2_line_id, new_value="750")

    result = await apply_proposed_change(pc_id, "reviewer")

    assert result["status"] == "applied"
    line = await fetch_one(
        "SELECT quantity, version FROM purchase_order_line WHERE id = %s",
        (seed.sku2_line_id,),
    )
    assert float(line["quantity"]) == 750.0
    assert line["version"] == 2


async def test_apply_writes_an_audit_row_linked_to_the_proposal(
    seed, make_proposal, fetch_one
):
    pc_id = await make_proposal(seed.sku2_line_id, old_value="200.0000", new_value="750")

    await apply_proposed_change(pc_id, "reviewer")

    audit = await fetch_one("SELECT * FROM audit_log")
    assert audit["target_table"] == "purchase_order_line"
    assert str(audit["target_record_id"]) == seed.sku2_line_id
    assert audit["field_name"] == "quantity"
    assert audit["prior_value"] == "200.0000"
    assert audit["new_value"] == "750"
    assert audit["applied_by"] == "reviewer"
    assert str(audit["proposed_change_id"]) == pc_id


async def test_apply_marks_the_proposal_applied(seed, make_proposal, fetch_one):
    pc_id = await make_proposal(seed.sku2_line_id)

    await apply_proposed_change(pc_id)

    pc = await fetch_one("SELECT status FROM proposed_changes WHERE id = %s", (pc_id,))
    assert pc["status"] == "applied"


async def test_apply_can_write_a_date(seed, make_proposal, fetch_one):
    pc_id = await make_proposal(
        seed.sku2_line_id,
        field_name="delivery_date",
        old_value="2026-01-15",
        new_value="2026-02-03",
    )

    result = await apply_proposed_change(pc_id)

    assert result["status"] == "applied"
    line = await fetch_one(
        "SELECT delivery_date FROM purchase_order_line WHERE id = %s",
        (seed.sku2_line_id,),
    )
    assert line["delivery_date"].isoformat() == "2026-02-03"


# ── optimistic locking ───────────────────────────────────────────────────────


async def test_apply_is_superseded_when_the_row_moved_on(
    seed, make_proposal, fetch_one
):
    """Someone else updated the line after the change was proposed."""
    pc_id = await make_proposal(seed.sku2_line_id, new_value="750")

    # An unrelated writer bumps the row's version.
    async with connection() as conn:
        await conn.execute(
            "UPDATE purchase_order_line SET quantity = 999 WHERE id = %s",
            (seed.sku2_line_id,),
        )

    result = await apply_proposed_change(pc_id)

    assert result["status"] == "superseded"
    pc = await fetch_one("SELECT status FROM proposed_changes WHERE id = %s", (pc_id,))
    assert pc["status"] == "superseded"

    line = await fetch_one(
        "SELECT quantity FROM purchase_order_line WHERE id = %s", (seed.sku2_line_id,)
    )
    assert float(line["quantity"]) == 999.0  # the other writer's value survives


async def test_a_superseded_apply_writes_no_audit_row(seed, make_proposal, fetch_one):
    pc_id = await make_proposal(seed.sku2_line_id)
    async with connection() as conn:
        await conn.execute(
            "UPDATE purchase_order_line SET quantity = 999 WHERE id = %s",
            (seed.sku2_line_id,),
        )

    await apply_proposed_change(pc_id)

    assert await fetch_one("SELECT * FROM audit_log") is None


async def test_two_proposals_on_one_row_leave_only_the_first_applied(
    seed, make_proposal, fetch_one, fetch_all
):
    """Both were staged against version 1; only one can win."""
    first = await make_proposal(seed.sku2_line_id, new_value="750")
    second = await make_proposal(seed.sku2_line_id, new_value="800")

    assert (await apply_proposed_change(first))["status"] == "applied"
    assert (await apply_proposed_change(second))["status"] == "superseded"

    line = await fetch_one(
        "SELECT quantity, version FROM purchase_order_line WHERE id = %s",
        (seed.sku2_line_id,),
    )
    assert float(line["quantity"]) == 750.0
    assert line["version"] == 2

    audit = await fetch_all("SELECT * FROM audit_log")
    assert len(audit) == 1


async def test_concurrent_applies_serialise_to_one_winner(
    seed, make_proposal, fetch_one, fetch_all
):
    """Fire both applies at once; FOR UPDATE must make them queue, not race."""
    first = await make_proposal(seed.sku2_line_id, new_value="750")
    second = await make_proposal(seed.sku2_line_id, new_value="800")

    results = await asyncio.gather(
        apply_proposed_change(first, "reviewer-a"),
        apply_proposed_change(second, "reviewer-b"),
    )

    statuses = sorted(r["status"] for r in results)
    assert statuses == ["applied", "superseded"]

    line = await fetch_one(
        "SELECT quantity, version FROM purchase_order_line WHERE id = %s",
        (seed.sku2_line_id,),
    )
    assert line["version"] == 2
    assert float(line["quantity"]) in (750.0, 800.0)

    # Exactly one canonical write means exactly one audit row.
    assert len(await fetch_all("SELECT * FROM audit_log")) == 1


async def test_apply_survives_an_external_writer_holding_the_row(
    seed, make_proposal, fetch_one, raw_connect
):
    """A lock held by another session blocks, then resolves as superseded."""
    pc_id = await make_proposal(seed.sku2_line_id, new_value="750")

    other = raw_connect()
    other.execute(
        "SELECT version FROM purchase_order_line WHERE id = %s FOR UPDATE",
        (seed.sku2_line_id,),
    )
    other.execute(
        "UPDATE purchase_order_line SET quantity = 42 WHERE id = %s",
        (seed.sku2_line_id,),
    )

    task = asyncio.create_task(apply_proposed_change(pc_id))
    await asyncio.sleep(0.2)
    assert not task.done()  # blocked on the other session's row lock

    other.commit()
    other.close()

    result = await task
    assert result["status"] == "superseded"

    line = await fetch_one(
        "SELECT quantity FROM purchase_order_line WHERE id = %s", (seed.sku2_line_id,)
    )
    assert float(line["quantity"]) == 42.0


# ── refusals ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["pending", "rejected", "applied", "superseded"])
async def test_apply_refuses_a_proposal_that_is_not_approved(
    seed, make_proposal, status
):
    pc_id = await make_proposal(seed.sku2_line_id, status=status)

    with pytest.raises(NotApplicableError, match="expected 'approved'"):
        await apply_proposed_change(pc_id)


async def test_apply_refuses_an_unknown_proposal():
    with pytest.raises(ProposedChangeNotFoundError):
        await apply_proposed_change("00000000-0000-0000-0000-000000000000")


async def test_apply_refuses_a_field_outside_the_whitelist(
    seed, make_proposal, fetch_one
):
    """field_name comes from the database, so it is never interpolated blindly."""
    pc_id = await make_proposal(seed.sku2_line_id)
    async with connection() as conn:
        await conn.execute(
            "UPDATE proposed_changes SET field_name = %s WHERE id = %s",
            ("quantity; DROP TABLE audit_log", pc_id),
        )

    with pytest.raises(NotApplicableError, match="unsupported field_name"):
        await apply_proposed_change(pc_id)

    assert await fetch_one("SELECT to_regclass('audit_log') AS t") is not None


# ── audit immutability ───────────────────────────────────────────────────────


async def test_audit_rows_cannot_be_updated_or_deleted(seed, make_proposal):
    pc_id = await make_proposal(seed.sku2_line_id)
    await apply_proposed_change(pc_id)

    async with connection() as conn:
        with pytest.raises(Exception, match="immutable"):
            await conn.execute("UPDATE audit_log SET new_value = 'tampered'")

    async with connection() as conn:
        with pytest.raises(Exception, match="immutable"):
            await conn.execute("DELETE FROM audit_log")
