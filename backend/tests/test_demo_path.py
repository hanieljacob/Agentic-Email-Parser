"""The demo path.

`pnpm seed` is what a reviewer runs, so what it produces is worth pinning:
a populated review queue, the same on every machine, with no network call.
This runs the real seed and the real fixture loader against the test
database and asserts the exact outcome the README documents.
"""

from __future__ import annotations

from backend.scripts.load_fixtures import load_fixtures
from backend.scripts.seed import seed


async def _seed_demo() -> dict[str, int]:
    await seed()
    return await load_fixtures()


async def test_seed_produces_the_documented_queue():
    totals = await _seed_demo()

    assert totals == {
        "emails": 3,
        "proposed": 3,
        "auto_applied": 1,
        "pending": 2,
    }


async def test_the_clean_email_is_applied_to_canonical_data(fetch_one):
    await _seed_demo()

    line = await fetch_one(
        """
        SELECT pol.delivery_date, pol.version
        FROM purchase_order_line pol
        JOIN purchase_order po ON po.id = pol.purchase_order_id
        JOIN product p         ON p.id = pol.product_id
        WHERE po.reference_num = 'PO-12' AND p.sku = 'SKU-2'
        """
    )
    assert line["delivery_date"].isoformat() == "2026-02-03"
    assert line["version"] == 2


async def test_the_auto_applied_change_is_audited(fetch_all):
    await _seed_demo()

    audit = await fetch_all("SELECT * FROM audit_log")
    assert len(audit) == 1
    assert audit[0]["applied_by"] == "auto"
    assert audit[0]["field_name"] == "delivery_date"
    assert audit[0]["new_value"] == "2026-02-03"
    assert audit[0]["prior_value"] == "2026-01-15"


async def test_the_queue_holds_the_two_uncertain_changes(fetch_all):
    await _seed_demo()

    rows = await fetch_all(
        """
        SELECT p.sku, pc.field_name, pc.new_value, pc.combined_confidence
        FROM proposed_changes pc
        JOIN purchase_order_line pol ON pol.id = pc.target_record_id
        JOIN product p               ON p.id = pol.product_id
        WHERE pc.status = 'pending'
        ORDER BY pc.combined_confidence DESC
        """
    )

    assert [(r["sku"], float(r["combined_confidence"])) for r in rows] == [
        # matched only through the supplier's own code "SKU13" → 1.0 × 0.9
        ("SKU-1-3", 0.9),
        # the model inferred the date from "sometime in early March" → 0.8 × 1.0
        ("SKU-1", 0.8),
    ]


async def test_nothing_pending_has_touched_canonical_data(fetch_one):
    await _seed_demo()

    quantity = await fetch_one(
        """
        SELECT pol.quantity
        FROM purchase_order_line pol
        JOIN purchase_order po ON po.id = pol.purchase_order_id
        JOIN product p         ON p.id = pol.product_id
        WHERE po.reference_num = 'PO-12' AND p.sku = 'SKU-1-3'
        """
    )
    assert float(quantity["quantity"]) == 15000.0  # still the seeded value


async def test_seeding_twice_is_idempotent(fetch_all):
    await _seed_demo()
    await _seed_demo()

    pending = await fetch_all(
        "SELECT id FROM proposed_changes WHERE status = 'pending'"
    )
    emails = await fetch_all("SELECT id FROM emails")

    assert len(pending) == 2
    assert len(emails) == 3
