"""Matching step: extraction_run_id → proposed_changes rows.

Resolves the `po_ref` and `sku_or_code` strings the model produced back to
canonical database records, scores how confident the whole chain is, and
stages one `proposed_changes` row per line update.

Confidence is a product of two independent things:

    match_confidence    = po_match_confidence × line_match_confidence
    combined_confidence = extraction_confidence × match_confidence

where an exact reference/SKU hit scores 1.0 and a normalised or
supplier-alias hit scores 0.9. Anything at or above AUTO_APPLY_THRESHOLD is
staged as `approved` and written back immediately; everything else is left
`pending` for a human. Because the prompt only ever emits extraction
confidences of 1.0, 0.8 or 0.6, the default threshold of 0.95 means
auto-apply requires an unambiguous statement matched to an exact PO
reference and an exact SKU — any inference or alias hop routes to review.
"""

from __future__ import annotations

import logging
import re
from typing import Any, TypedDict

from psycopg import AsyncConnection

from backend.config import get_settings
from backend.db import connection
from backend.suppliers import resolve_supplier
from backend.writeback import apply_proposed_change

log = logging.getLogger(__name__)


class UnmatchedSku(TypedDict):
    po_ref: str
    sku_or_code: str


class MatchSummary(TypedDict):
    proposed: int
    auto_applied: int
    unmatched_pos: list[str]
    unmatched_skus: list[UnmatchedSku]


class ExtractionRunNotFoundError(LookupError):
    pass


# ── normalization ────────────────────────────────────────────────────────────

_PO_PREFIX = re.compile(r"^PO(?=[-_\s]|\d)")
_LEADING_SEP = re.compile(r"^[-_\s]+")
_SEPARATORS = re.compile(r"[\s\-_]")
_ZERO_RUN = re.compile(r"0+(\d)")


def normalize_ref(s: str) -> str:
    """Loosen a PO reference so "PO-12", "12" and "PO12" compare equal.

    Ported verbatim from the TypeScript matcher, including the fact that the
    final rule collapses zero runs anywhere in the string rather than only
    leading ones — so "PO-010" and "PO-10" both normalise to "1".
    """
    s = s.upper()
    # Strip common "PO" prefix when followed by a separator or digit
    # so "PO-12" ~ "12" ~ "PO12" but "POMADE" is left alone
    s = _PO_PREFIX.sub("", s)
    s = _LEADING_SEP.sub("", s)  # strip any leading separator left behind
    s = _SEPARATORS.sub("", s)  # strip remaining separators
    return _ZERO_RUN.sub(r"\1", s)  # collapse leading zeros in digit runs


# ── PO resolution ────────────────────────────────────────────────────────────


async def resolve_po(
    conn: AsyncConnection,
    supplier_id: str,
    po_ref: str,
) -> tuple[dict[str, Any], float] | None:
    """Resolve a quoted PO reference to a purchase_order row."""
    # 1. Exact match
    cur = await conn.execute(
        """
        SELECT id, version, reference_num
        FROM purchase_order
        WHERE supplier_id = %s AND reference_num = %s
        """,
        (supplier_id, po_ref),
    )
    exact = await cur.fetchone()
    if exact is not None:
        return exact, 1.0

    # 2. Normalized match — load all POs for supplier and compare in memory
    cur = await conn.execute(
        "SELECT id, version, reference_num FROM purchase_order WHERE supplier_id = %s",
        (supplier_id,),
    )
    norm = normalize_ref(po_ref)
    for row in await cur.fetchall():
        if normalize_ref(row["reference_num"]) == norm:
            return row, 0.9

    return None


# ── line resolution ──────────────────────────────────────────────────────────


async def resolve_line(
    conn: AsyncConnection,
    po_id: str,
    supplier_id: str,
    sku_or_code: str,
) -> tuple[dict[str, Any], float] | None:
    """Resolve a quoted product code to a line on this purchase order."""
    # 1. Exact SKU match on lines of this PO
    cur = await conn.execute(
        """
        SELECT pol.id, pol.version, pol.quantity, pol.delivery_date, p.sku
        FROM   purchase_order_line pol
        JOIN   product p ON p.id = pol.product_id
        WHERE  pol.purchase_order_id = %s AND p.sku = %s
        LIMIT  1
        """,
        (po_id, sku_or_code),
    )
    exact = await cur.fetchone()
    if exact is not None:
        return exact, 1.0

    # 2. Supplier SKU alias match via supplier_product
    cur = await conn.execute(
        """
        SELECT pol.id, pol.version, pol.quantity, pol.delivery_date, p.sku
        FROM   purchase_order_line pol
        JOIN   product p            ON p.id = pol.product_id
        JOIN   supplier_product sp  ON sp.product_id = p.id
        WHERE  pol.purchase_order_id = %s
          AND  sp.supplier_id = %s
          AND  sp.supplier_sku = %s
        LIMIT  1
        """,
        (po_id, supplier_id, sku_or_code),
    )
    alias = await cur.fetchone()
    if alias is not None:
        return alias, 0.9

    return None


# ── old value snapshot ───────────────────────────────────────────────────────


def get_old_value(line: dict[str, Any], field: str) -> str | None:
    """The canonical value at proposal time, shown to the reviewer as 'before'."""
    if field == "delivery_date":
        return line["delivery_date"].isoformat() if line["delivery_date"] else None
    if field == "quantity":
        return str(line["quantity"])
    return None


# ── core match function ──────────────────────────────────────────────────────


async def match(run_id: str, threshold: float | None = None) -> MatchSummary:
    """Stage proposed changes for one extraction run, auto-applying the safe ones."""
    if threshold is None:
        threshold = get_settings().auto_apply_threshold

    summary: MatchSummary = {
        "proposed": 0,
        "auto_applied": 0,
        "unmatched_pos": [],
        "unmatched_skus": [],
    }
    auto_apply_ids: list[str] = []

    async with connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                "SELECT email_id, llm_output FROM extraction_runs WHERE id = %s",
                (run_id,),
            )
            run = await cur.fetchone()
            if run is None:
                raise ExtractionRunNotFoundError(f"extraction_run not found: {run_id}")

            email_id = run["email_id"]
            llm_output = run["llm_output"] or {}

            # Resolve supplier from email sender
            cur = await conn.execute(
                "SELECT sender FROM emails WHERE id = %s", (email_id,)
            )
            email_row = await cur.fetchone()
            sender = email_row["sender"] if email_row else ""

            supplier = await resolve_supplier(conn, sender)
            supplier_id = supplier["id"] if supplier else None

            for po_update in llm_output.get("po_updates") or []:
                if supplier_id is None:
                    summary["unmatched_pos"].append(po_update["po_ref"])
                    continue

                po_match = await resolve_po(conn, supplier_id, po_update["po_ref"])
                if po_match is None:
                    summary["unmatched_pos"].append(po_update["po_ref"])
                    continue
                po_row, po_confidence = po_match

                for lu in po_update.get("line_updates") or []:
                    line_match = await resolve_line(
                        conn, po_row["id"], supplier_id, lu["sku_or_code"]
                    )
                    if line_match is None:
                        summary["unmatched_skus"].append(
                            {
                                "po_ref": po_update["po_ref"],
                                "sku_or_code": lu["sku_or_code"],
                            }
                        )
                        continue
                    line_row, line_confidence = line_match

                    extraction_confidence = lu["confidence"]
                    match_confidence = po_confidence * line_confidence
                    combined_confidence = extraction_confidence * match_confidence
                    will_auto_apply = combined_confidence >= threshold

                    cur = await conn.execute(
                        """
                        INSERT INTO proposed_changes
                          (email_id, extraction_run_id,
                           target_table, target_record_id, target_record_version,
                           field_name, old_value, new_value,
                           evidence_text,
                           extraction_confidence, match_confidence, combined_confidence,
                           status)
                        VALUES (%s,%s,'purchase_order_line',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        RETURNING id
                        """,
                        (
                            email_id,
                            run_id,
                            line_row["id"],
                            line_row["version"],
                            lu["field"],
                            get_old_value(line_row, lu["field"]),
                            lu["new_value"],
                            lu["evidence"],
                            extraction_confidence,
                            match_confidence,
                            combined_confidence,
                            "approved" if will_auto_apply else "pending",
                        ),
                    )
                    inserted = await cur.fetchone()
                    assert inserted is not None
                    summary["proposed"] += 1
                    if will_auto_apply:
                        auto_apply_ids.append(str(inserted["id"]))

            any_unmatched = bool(summary["unmatched_pos"] or summary["unmatched_skus"])
            await conn.execute(
                "UPDATE emails SET status = %s WHERE id = %s",
                ("needs_review" if any_unmatched else "matched", email_id),
            )

    # Run auto-apply after the match transaction commits; each apply is its
    # own transaction, so one failure cannot roll back the staged proposals.
    for pc_id in auto_apply_ids:
        try:
            result = await apply_proposed_change(pc_id, "auto")
            if result["status"] == "applied":
                summary["auto_applied"] += 1
        except Exception as err:
            log.error("auto-apply failed for %s: %s", pc_id, err)

    return summary
