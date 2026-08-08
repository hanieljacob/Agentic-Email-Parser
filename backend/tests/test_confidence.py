"""Confidence scoring.

combined_confidence = extraction_confidence × po_match × line_match, where
an exact hit scores 1.0 and a normalised or alias hit scores 0.9. These
tests pin both the component scores and the arithmetic.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from backend.config import Settings
from backend.db import connection
from backend.match import get_old_value, match, normalize_ref, resolve_line, resolve_po


# ── reference normalisation ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PO-12", "12"),
        ("po-12", "12"),
        ("PO12", "12"),
        ("12", "12"),
        ("PO 12", "12"),
        ("PO_12", "12"),
        # leading whitespace defeats the ^PO strip, so the prefix survives
        ("  PO-12", "PO12"),
        ("POMADE", "POMADE"),  # "PO" prefix only stripped before a separator or digit
        ("PO-0012", "12"),
    ],
)
def test_normalize_ref(raw, expected):
    assert normalize_ref(raw) == expected


def test_normalize_ref_collapses_zero_runs_anywhere():
    """Ported bug, kept deliberately: the zero-collapse is not anchored.

    The final rule is meant to strip *leading* zeros but is unanchored, so it
    eats a zero anywhere it is followed by a digit. Genuinely different
    references therefore collide on the fuzzy path:

        PO-10, PO-010, PO-100, PO-1000  ->  "10"
        PO-12, PO-102                   ->  "12"

    Not reachable with the current seed data, and preserved rather than fixed
    so the port stays faithful. It is contained by the confidence model: a
    normalised PO hit scores 0.9, so combined_confidence can never reach the
    0.95 threshold and a mis-resolved reference always lands in review rather
    than being written back automatically.
    """
    assert normalize_ref("PO-10") == "10"
    assert normalize_ref("PO-010") == "10"
    assert normalize_ref("PO-100") == "10"
    assert normalize_ref("PO-1000") == "10"
    assert normalize_ref("PO-12") == normalize_ref("PO-102") == "12"


# ── component confidences ────────────────────────────────────────────────────


async def test_exact_po_reference_scores_one(seed):
    async with connection() as conn:
        result = await resolve_po(conn, seed.supplier_id, "PO-12")
    assert result is not None
    row, confidence = result
    assert row["reference_num"] == "PO-12"
    assert confidence == 1.0


async def test_normalised_po_reference_scores_zero_point_nine(seed):
    async with connection() as conn:
        result = await resolve_po(conn, seed.supplier_id, "po 12")
    assert result is not None
    row, confidence = result
    assert row["reference_num"] == "PO-12"
    assert confidence == 0.9


async def test_unknown_po_reference_does_not_resolve(seed):
    async with connection() as conn:
        assert await resolve_po(conn, seed.supplier_id, "PO-999") is None


async def test_exact_sku_scores_one(seed):
    async with connection() as conn:
        result = await resolve_line(conn, seed.po12_id, seed.supplier_id, "SKU-2")
    assert result is not None
    row, confidence = result
    assert str(row["id"]) == seed.sku2_line_id
    assert confidence == 1.0


async def test_supplier_alias_sku_scores_zero_point_nine(seed):
    """"SKU13" is the supplier's own code for SKU-1-3."""
    async with connection() as conn:
        result = await resolve_line(conn, seed.po12_id, seed.supplier_id, "SKU13")
    assert result is not None
    row, confidence = result
    assert str(row["id"]) == seed.alias_line_id
    assert confidence == 0.9


async def test_unknown_sku_does_not_resolve(seed):
    async with connection() as conn:
        assert await resolve_line(conn, seed.po12_id, seed.supplier_id, "NOPE") is None


# ── the product ──────────────────────────────────────────────────────────────


def _line_update(sku: str, confidence: float, field: str = "quantity", value: str = "500"):
    return {
        "sku_or_code": sku,
        "field": field,
        "new_value": value,
        "evidence": "reducing to 500",
        "confidence": confidence,
    }


@pytest.mark.parametrize(
    ("po_ref", "sku", "extraction_confidence", "expected_match", "expected_combined"),
    [
        # exact PO × exact SKU
        ("PO-12", "SKU-2", 1.0, 1.0, 1.0),
        ("PO-12", "SKU-2", 0.8, 1.0, 0.8),
        ("PO-12", "SKU-2", 0.6, 1.0, 0.6),
        # exact PO × alias SKU
        ("PO-12", "SKU13", 1.0, 0.9, 0.9),
        ("PO-12", "SKU13", 0.8, 0.9, 0.72),
        # normalised PO × exact SKU
        ("po 12", "SKU-2", 1.0, 0.9, 0.9),
        # normalised PO × alias SKU
        ("po 12", "SKU13", 1.0, 0.81, 0.81),
    ],
)
async def test_combined_confidence_is_the_product_of_its_parts(
    seed,
    make_run,
    fetch_one,
    po_ref,
    sku,
    extraction_confidence,
    expected_match,
    expected_combined,
):
    _, run_id = await make_run(
        {
            "po_updates": [
                {
                    "po_ref": po_ref,
                    "source": "body",
                    "evidence": "e",
                    "confidence": 1.0,
                    "line_updates": [_line_update(sku, extraction_confidence)],
                }
            ],
            "unmatched_mentions": [],
        }
    )

    # Threshold above 1.0 keeps everything pending so the stored scores are
    # what this test reads back, undisturbed by writeback.
    await match(run_id, threshold=1.1)

    pc = await fetch_one(
        "SELECT * FROM proposed_changes WHERE extraction_run_id = %s", (run_id,)
    )
    assert float(pc["extraction_confidence"]) == pytest.approx(extraction_confidence)
    assert float(pc["match_confidence"]) == pytest.approx(expected_match)
    assert float(pc["combined_confidence"]) == pytest.approx(expected_combined)


# ── old-value snapshot ───────────────────────────────────────────────────────


def test_get_old_value_renders_a_date_as_iso():
    line = {"delivery_date": dt.date(2026, 1, 15), "quantity": Decimal("200.0000")}
    assert get_old_value(line, "delivery_date") == "2026-01-15"


def test_get_old_value_preserves_numeric_scale():
    line = {"delivery_date": None, "quantity": Decimal("200.0000")}
    assert get_old_value(line, "quantity") == "200.0000"


def test_get_old_value_handles_an_unset_date():
    assert get_old_value({"delivery_date": None, "quantity": 1}, "delivery_date") is None


def test_get_old_value_ignores_unknown_fields():
    assert get_old_value({"delivery_date": None, "quantity": 1}, "unit_price") is None


async def test_old_value_is_snapshotted_onto_the_proposal(seed, make_run, fetch_one):
    _, run_id = await make_run(
        {
            "po_updates": [
                {
                    "po_ref": "PO-12",
                    "source": "body",
                    "evidence": "e",
                    "confidence": 1.0,
                    "line_updates": [_line_update("SKU-2", 1.0)],
                }
            ],
            "unmatched_mentions": [],
        }
    )
    await match(run_id, threshold=1.1)

    pc = await fetch_one(
        "SELECT old_value, new_value FROM proposed_changes WHERE extraction_run_id = %s",
        (run_id,),
    )
    assert pc["old_value"] == "200.0000"
    assert pc["new_value"] == "500"


# ── the threshold itself ─────────────────────────────────────────────────────


def test_default_auto_apply_threshold_is_zero_point_nine_five():
    assert Settings.model_fields["auto_apply_threshold"].default == 0.95
