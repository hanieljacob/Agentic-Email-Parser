"""The two correction flows the review UI calls.

Both write learned state that later extractions read back — an alias makes a
sender resolvable, a correction is replayed into the prompt as a few-shot
example — so getting them wrong degrades quietly rather than loudly.
"""

from __future__ import annotations

import json

import pytest

from backend.db import connection
from backend.learning import (
    EmailNotFoundError,
    ProposedChangeNotFoundError,
    SupplierUnresolvedError,
    assign_supplier,
    correct_sku,
)

EVIDENCE = "we can only ship 12000 of the usual item"


async def _product_id(sku: str) -> str:
    async with connection() as conn:
        cur = await conn.execute("SELECT id FROM product WHERE sku = %s", (sku,))
        return str((await cur.fetchone())["id"])


async def _proposal_with_run(make_email, seed, *, sku_in_output: str, target_line: str):
    """A proposal whose extraction run still holds the code the model used."""
    email_id = await make_email(subject="PO-12 update")
    llm_output = {
        "po_updates": [
            {
                "po_ref": "PO-12",
                "source": "body",
                "evidence": "PO-12",
                "confidence": 1.0,
                "line_updates": [
                    {
                        "sku_or_code": sku_in_output,
                        "field": "quantity",
                        "new_value": "12000",
                        "evidence": EVIDENCE,
                        "confidence": 1.0,
                    }
                ],
            }
        ],
        "unmatched_mentions": [],
    }

    async with connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO extraction_runs (email_id, model_version, llm_output, status)
            VALUES (%s, 'test-model', %s, 'success') RETURNING id
            """,
            (email_id, json.dumps(llm_output)),
        )
        run_id = str((await cur.fetchone())["id"])

        cur = await conn.execute(
            """
            INSERT INTO proposed_changes
              (email_id, extraction_run_id, target_table, target_record_id,
               target_record_version, field_name, old_value, new_value,
               evidence_text, extraction_confidence, match_confidence,
               combined_confidence, status)
            VALUES (%s,%s,'purchase_order_line',%s,1,'quantity','200','12000',
                    %s,1.0,1.0,1.0,'pending')
            RETURNING id
            """,
            (email_id, run_id, target_line, EVIDENCE),
        )
        return email_id, str((await cur.fetchone())["id"])


# ── assign_supplier ──────────────────────────────────────────────────────────


async def test_assign_supplier_records_the_alias(seed, make_email, fetch_one):
    email_id = await make_email(sender="Procurement <orders@bigsupplier.co.uk>")

    result = await assign_supplier(email_id, seed.supplier_id, retrigger=False)

    assert result["ok"] is True
    assert result["alias_inserted"] is True
    assert result["retriggered"] is False

    alias = await fetch_one(
        "SELECT supplier_id FROM supplier_email_aliases WHERE email_address = %s",
        ("orders@bigsupplier.co.uk",),
    )
    assert str(alias["supplier_id"]) == seed.supplier_id


async def test_assign_supplier_stores_only_the_bare_address(
    seed, make_email, fetch_all
):
    """The display name must not end up in the alias table."""
    email_id = await make_email(sender='"Big Supplier Ltd" <ORDERS@Example.COM>')

    await assign_supplier(email_id, seed.supplier_id, retrigger=False)

    addresses = [
        r["email_address"]
        for r in await fetch_all("SELECT email_address FROM supplier_email_aliases")
    ]
    assert "orders@example.com" in addresses


async def test_assigning_the_same_alias_twice_is_harmless(seed, make_email, fetch_all):
    email_id = await make_email(sender="ops@newdomain.test")

    first = await assign_supplier(email_id, seed.supplier_id, retrigger=False)
    second = await assign_supplier(email_id, seed.supplier_id, retrigger=False)

    assert first["alias_inserted"] is True
    assert second["alias_inserted"] is False
    rows = await fetch_all(
        "SELECT id FROM supplier_email_aliases WHERE email_address = %s",
        ("ops@newdomain.test",),
    )
    assert len(rows) == 1


async def test_assign_supplier_makes_the_sender_resolvable(seed, make_email):
    """The point of the alias: the next extraction can find the supplier."""
    from backend.suppliers import resolve_supplier

    email_id = await make_email(sender="ops@newdomain.test")
    async with connection() as conn:
        assert await resolve_supplier(conn, "ops@newdomain.test") is None

    await assign_supplier(email_id, seed.supplier_id, retrigger=False)

    async with connection() as conn:
        supplier = await resolve_supplier(conn, "ops@newdomain.test")
    assert supplier is not None
    assert str(supplier["id"]) == seed.supplier_id


async def test_assign_supplier_rejects_an_unknown_email(seed):
    with pytest.raises(EmailNotFoundError):
        await assign_supplier(
            "00000000-0000-0000-0000-000000000000", seed.supplier_id, retrigger=False
        )


async def test_retrigger_failure_does_not_lose_the_alias(
    seed, make_email, fetch_one, monkeypatch
):
    """Re-running the pipeline is best-effort; the alias insert is not."""

    async def boom(*args, **kwargs):
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr("backend.learning.run_pipeline", boom)
    email_id = await make_email(sender="ops@newdomain.test")

    result = await assign_supplier(email_id, seed.supplier_id, retrigger=True)

    assert result["alias_inserted"] is True
    assert result["retriggered"] is False
    assert await fetch_one(
        "SELECT id FROM supplier_email_aliases WHERE email_address = %s",
        ("ops@newdomain.test",),
    )


# ── correct_sku ──────────────────────────────────────────────────────────────


async def test_correct_sku_learns_the_supplier_code(
    seed, make_email, fetch_one
):
    correct_product = await _product_id("SKU-1")
    _, change_id = await _proposal_with_run(
        make_email, seed, sku_in_output="THEIR-CODE-9", target_line=seed.sku2_line_id
    )

    result = await correct_sku(change_id, correct_product)

    assert result["ok"] is True
    assert result["supplier_sku"] == "THEIR-CODE-9"

    mapping = await fetch_one(
        "SELECT supplier_sku FROM supplier_product WHERE supplier_id = %s AND product_id = %s",
        (seed.supplier_id, correct_product),
    )
    assert mapping["supplier_sku"] == "THEIR-CODE-9"


async def test_correct_sku_repoints_the_proposal_at_the_right_line(
    seed, make_email, fetch_one
):
    correct_product = await _product_id("SKU-1")
    _, change_id = await _proposal_with_run(
        make_email, seed, sku_in_output="THEIR-CODE-9", target_line=seed.sku2_line_id
    )

    result = await correct_sku(change_id, correct_product)

    assert result["line_updated"] is True
    pc = await fetch_one(
        "SELECT target_record_id, target_record_version FROM proposed_changes WHERE id = %s",
        (change_id,),
    )
    assert str(pc["target_record_id"]) == seed.sku1_line_id
    # The version must be re-snapshotted against the new target, or the apply
    # path would compare against a version belonging to a different row.
    assert pc["target_record_version"] == 1


async def test_correct_sku_records_a_few_shot_correction(
    seed, make_email, fetch_one
):
    correct_product = await _product_id("SKU-1")
    _, change_id = await _proposal_with_run(
        make_email, seed, sku_in_output="THEIR-CODE-9", target_line=seed.sku2_line_id
    )

    await correct_sku(change_id, correct_product)

    correction = await fetch_one("SELECT * FROM supplier_corrections")
    assert correction["context"] == EVIDENCE
    assert correction["wrong"] == "THEIR-CODE-9"
    assert correction["correct"] == "SKU-1"
    assert correction["field"] == "sku_or_code"


async def test_correct_sku_feeds_the_next_prompt(seed, make_email):
    """The correction has to come back as context, or it taught nothing."""
    from backend.extract import load_context
    from backend.prompt import format_context

    correct_product = await _product_id("SKU-1")
    _, change_id = await _proposal_with_run(
        make_email, seed, sku_in_output="THEIR-CODE-9", target_line=seed.sku2_line_id
    )
    await correct_sku(change_id, correct_product)

    async with connection() as conn:
        supplier, pos, aliases, corrections = await load_context(
            conn, "Big Supplier <big@supplier.com>"
        )
    rendered = format_context(supplier, pos, aliases, corrections)

    assert "Past Extraction Corrections" in rendered
    assert "THEIR-CODE-9" in rendered
    assert "THEIR-CODE-9  →  SKU-1" in rendered or "THEIR-CODE-9" in rendered


async def test_correct_sku_without_a_matching_line_update_still_repoints(
    seed, make_email, fetch_one
):
    """Evidence that matches nothing in the run means no code can be learned."""
    correct_product = await _product_id("SKU-1")
    _, change_id = await _proposal_with_run(
        make_email, seed, sku_in_output="THEIR-CODE-9", target_line=seed.sku2_line_id
    )
    async with connection() as conn:
        await conn.execute(
            "UPDATE proposed_changes SET evidence_text = 'something else' WHERE id = %s",
            (change_id,),
        )

    result = await correct_sku(change_id, correct_product)

    assert result["supplier_sku"] is None
    assert result["line_updated"] is True
    assert await fetch_one("SELECT * FROM supplier_corrections") is None


async def test_correct_sku_rejects_an_unknown_proposal(seed):
    with pytest.raises(ProposedChangeNotFoundError):
        await correct_sku(
            "00000000-0000-0000-0000-000000000000",
            await _product_id("SKU-1"),
        )


async def test_correct_sku_needs_a_resolvable_supplier(seed, make_email):
    correct_product = await _product_id("SKU-1")
    _, change_id = await _proposal_with_run(
        make_email, seed, sku_in_output="X", target_line=seed.sku2_line_id
    )
    async with connection() as conn:
        await conn.execute(
            "UPDATE emails SET sender = 'nobody@nowhere.test' WHERE id IN "
            "(SELECT email_id FROM proposed_changes WHERE id = %s)",
            (change_id,),
        )

    with pytest.raises(SupplierUnresolvedError):
        await correct_sku(change_id, correct_product)
