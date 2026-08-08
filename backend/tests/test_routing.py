"""Auto-apply versus review routing.

The decision the whole system turns on: a staged change is written back
immediately when combined_confidence >= AUTO_APPLY_THRESHOLD, and parked for
a human otherwise. These tests assert the routing *and* that the losing side
genuinely does not touch canonical data.
"""

from __future__ import annotations

import pytest

from backend.match import match

THRESHOLD = 0.95


def _run(po_ref: str, sku: str, confidence: float, field="quantity", value="500"):
    return {
        "po_updates": [
            {
                "po_ref": po_ref,
                "source": "body",
                "evidence": "reducing to 500",
                "confidence": 1.0,
                "line_updates": [
                    {
                        "sku_or_code": sku,
                        "field": field,
                        "new_value": value,
                        "evidence": "reducing to 500",
                        "confidence": confidence,
                    }
                ],
            }
        ],
        "unmatched_mentions": [],
    }


# ── above the threshold ──────────────────────────────────────────────────────


async def test_high_confidence_change_is_auto_applied(seed, make_run, fetch_one):
    _, run_id = await make_run(_run("PO-12", "SKU-2", 1.0))

    summary = await match(run_id, threshold=THRESHOLD)

    assert summary["proposed"] == 1
    assert summary["auto_applied"] == 1

    pc = await fetch_one(
        "SELECT * FROM proposed_changes WHERE extraction_run_id = %s", (run_id,)
    )
    assert pc["status"] == "applied"

    line = await fetch_one(
        "SELECT quantity, version FROM purchase_order_line WHERE id = %s",
        (seed.sku2_line_id,),
    )
    assert float(line["quantity"]) == 500.0
    assert line["version"] == 2  # bumped by the increment_version trigger


async def test_auto_applied_change_is_audited_as_auto(seed, make_run, fetch_one):
    _, run_id = await make_run(_run("PO-12", "SKU-2", 1.0))
    await match(run_id, threshold=THRESHOLD)

    audit = await fetch_one("SELECT * FROM audit_log")
    assert audit["applied_by"] == "auto"
    assert audit["prior_value"] == "200.0000"
    assert audit["new_value"] == "500"
    assert audit["field_name"] == "quantity"


async def test_change_exactly_on_the_threshold_is_applied(seed, make_run, fetch_one):
    """The comparison is >=, not >."""
    _, run_id = await make_run(_run("PO-12", "SKU-2", THRESHOLD))

    summary = await match(run_id, threshold=THRESHOLD)

    assert summary["auto_applied"] == 1


# ── below the threshold ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("po_ref", "sku", "confidence", "combined", "why"),
    [
        ("PO-12", "SKU-2", 0.8, 0.8, "inferred value, exact match"),
        ("PO-12", "SKU-2", 0.6, 0.6, "uncertain value, exact match"),
        ("PO-12", "SKU13", 1.0, 0.9, "explicit value, supplier alias SKU"),
        ("po 12", "SKU-2", 1.0, 0.9, "explicit value, fuzzy PO reference"),
    ],
)
async def test_low_confidence_change_goes_to_review(
    seed, make_run, fetch_one, po_ref, sku, confidence, combined, why
):
    _, run_id = await make_run(_run(po_ref, sku, confidence))

    summary = await match(run_id, threshold=THRESHOLD)

    assert summary["proposed"] == 1
    assert summary["auto_applied"] == 0, why

    pc = await fetch_one(
        "SELECT * FROM proposed_changes WHERE extraction_run_id = %s", (run_id,)
    )
    assert pc["status"] == "pending"
    assert float(pc["combined_confidence"]) == pytest.approx(combined)

    assert await fetch_one("SELECT * FROM audit_log") is None


async def test_pending_change_leaves_canonical_data_untouched(
    seed, make_run, fetch_one
):
    _, run_id = await make_run(_run("PO-12", "SKU-2", 0.8))
    await match(run_id, threshold=THRESHOLD)

    line = await fetch_one(
        "SELECT quantity, version FROM purchase_order_line WHERE id = %s",
        (seed.sku2_line_id,),
    )
    assert float(line["quantity"]) == 200.0
    assert line["version"] == 1


# ── the threshold as a dial ──────────────────────────────────────────────────


async def test_raising_the_threshold_routes_everything_to_review(
    seed, make_run, fetch_one
):
    _, run_id = await make_run(_run("PO-12", "SKU-2", 1.0))

    summary = await match(run_id, threshold=1.1)

    assert summary["auto_applied"] == 0
    pc = await fetch_one(
        "SELECT status FROM proposed_changes WHERE extraction_run_id = %s", (run_id,)
    )
    assert pc["status"] == "pending"


async def test_threshold_of_zero_auto_applies_everything(seed, make_run):
    """Worth pinning: 0 does not disable auto-apply, it disables *review*.

    `combined_confidence >= 0` is true for every change, so a threshold of 0
    is the most permissive setting rather than the safest one. The env var
    documentation says so explicitly for this reason.
    """
    _, run_id = await make_run(_run("PO-12", "SKU-2", 0.6))

    summary = await match(run_id, threshold=0.0)

    assert summary["auto_applied"] == 1


# ── things that never resolve ────────────────────────────────────────────────


async def test_unknown_po_reference_is_reported_and_flags_the_email(
    seed, make_run, fetch_one
):
    email_id, run_id = await make_run(_run("PO-999", "SKU-2", 1.0))

    summary = await match(run_id, threshold=THRESHOLD)

    assert summary["proposed"] == 0
    assert summary["unmatched_pos"] == ["PO-999"]

    email = await fetch_one("SELECT status FROM emails WHERE id = %s", (email_id,))
    assert email["status"] == "needs_review"


async def test_unknown_sku_is_reported_and_flags_the_email(seed, make_run, fetch_one):
    email_id, run_id = await make_run(_run("PO-12", "SKU-NOPE", 1.0))

    summary = await match(run_id, threshold=THRESHOLD)

    assert summary["proposed"] == 0
    assert summary["unmatched_skus"] == [
        {"po_ref": "PO-12", "sku_or_code": "SKU-NOPE"}
    ]

    email = await fetch_one("SELECT status FROM emails WHERE id = %s", (email_id,))
    assert email["status"] == "needs_review"


async def test_email_from_an_unknown_sender_matches_nothing(
    seed, make_run, fetch_one
):
    _, run_id = await make_run(
        _run("PO-12", "SKU-2", 1.0), sender="stranger@elsewhere.com"
    )

    summary = await match(run_id, threshold=THRESHOLD)

    assert summary["proposed"] == 0
    assert summary["unmatched_pos"] == ["PO-12"]


async def test_fully_matched_email_is_marked_matched(seed, make_run, fetch_one):
    email_id, run_id = await make_run(_run("PO-12", "SKU-2", 0.8))

    await match(run_id, threshold=THRESHOLD)

    email = await fetch_one("SELECT status FROM emails WHERE id = %s", (email_id,))
    assert email["status"] == "matched"


# ── several changes in one email ─────────────────────────────────────────────


async def test_mixed_confidence_email_splits_between_both_routes(
    seed, make_run, fetch_all
):
    _, run_id = await make_run(
        {
            "po_updates": [
                {
                    "po_ref": "PO-12",
                    "source": "body",
                    "evidence": "e",
                    "confidence": 1.0,
                    "line_updates": [
                        {
                            "sku_or_code": "SKU-2",
                            "field": "delivery_date",
                            "new_value": "2026-02-03",
                            "evidence": "delivery now 3 February",
                            "confidence": 1.0,
                        },
                        {
                            "sku_or_code": "SKU13",
                            "field": "quantity",
                            "new_value": "12000",
                            "evidence": "we can only ship 12000",
                            "confidence": 1.0,
                        },
                        {
                            "sku_or_code": "SKU-1",
                            "field": "delivery_date",
                            "new_value": "2026-03-01",
                            "evidence": "sometime in March",
                            "confidence": 0.6,
                        },
                    ],
                }
            ],
            "unmatched_mentions": [],
        }
    )

    summary = await match(run_id, threshold=THRESHOLD)

    assert summary["proposed"] == 3
    assert summary["auto_applied"] == 1

    rows = await fetch_all(
        "SELECT status FROM proposed_changes WHERE extraction_run_id = %s", (run_id,)
    )
    statuses = sorted(r["status"] for r in rows)
    assert statuses == ["applied", "pending", "pending"]
