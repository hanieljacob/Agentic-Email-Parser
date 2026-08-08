"""Alias learning: the two correction paths called from the review UI.

assign_supplier — links an email sender address to a known supplier.
correct_sku     — teaches the system a supplier's own SKU for a product.

Both feed later extractions: the alias makes the sender resolvable, and the
correction is replayed into the prompt as a few-shot example.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from backend.db import connection
from backend.pipeline import run_pipeline
from backend.suppliers import parse_sender_email, resolve_supplier

log = logging.getLogger(__name__)


class AssignSupplierResult(TypedDict):
    ok: bool
    alias_inserted: bool
    retriggered: bool


class CorrectSkuResult(TypedDict):
    ok: bool
    supplier_sku: str | None
    line_updated: bool


class EmailNotFoundError(LookupError):
    pass


class ProposedChangeNotFoundError(LookupError):
    pass


class SupplierUnresolvedError(ValueError):
    pass


# ── assign_supplier ──────────────────────────────────────────────────────────


async def assign_supplier(
    email_id: str,
    supplier_id: str,
    retrigger: bool = True,
) -> AssignSupplierResult:
    """Record that this sender address belongs to a supplier."""
    async with connection() as conn:
        cur = await conn.execute(
            "SELECT sender FROM emails WHERE id = %s", (email_id,)
        )
        row = await cur.fetchone()
        if row is None:
            raise EmailNotFoundError(f"email not found: {email_id}")

        cur = await conn.execute(
            """
            INSERT INTO supplier_email_aliases (supplier_id, email_address)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (supplier_id, parse_sender_email(row["sender"])),
        )
        alias_inserted = cur.rowcount > 0

    retriggered = False
    if retrigger:
        try:
            await run_pipeline(email_id)
            retriggered = True
        except Exception as err:
            # Re-trigger is best-effort; don't fail the alias insert over it.
            log.error("retrigger failed for %s: %s", email_id, err)

    return AssignSupplierResult(
        ok=True, alias_inserted=alias_inserted, retriggered=retriggered
    )


# ── correct_sku ──────────────────────────────────────────────────────────────


def _find_extracted_sku(llm_output: dict[str, Any], pc: dict[str, Any]) -> str | None:
    """Recover the code the model used, by matching evidence + field.

    proposed_changes stores the resolved line, not the string the model
    produced, so the original code has to be found back in the run output.
    """
    for po_update in llm_output.get("po_updates") or []:
        for line_update in po_update.get("line_updates") or []:
            if (
                line_update.get("evidence") == pc["evidence_text"]
                and line_update.get("field") == pc["field_name"]
            ):
                return line_update.get("sku_or_code")
    return None


async def correct_sku(
    proposed_change_id: str,
    correct_product_id: str,
) -> CorrectSkuResult:
    """Repoint a proposal at the right product and learn the supplier's code."""
    async with connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                """
                SELECT pc.*, er.llm_output, e.sender
                FROM proposed_changes pc
                JOIN extraction_runs er ON er.id = pc.extraction_run_id
                JOIN emails e           ON e.id  = pc.email_id
                WHERE pc.id = %s
                """,
                (proposed_change_id,),
            )
            pc = await cur.fetchone()
            if pc is None:
                raise ProposedChangeNotFoundError(
                    f"proposed_change not found: {proposed_change_id}"
                )

            supplier = await resolve_supplier(conn, pc["sender"])
            if supplier is None:
                raise SupplierUnresolvedError(
                    "cannot resolve supplier for this email"
                )

            found_sku = _find_extracted_sku(pc["llm_output"] or {}, pc)

            cur = await conn.execute(
                "SELECT sku FROM product WHERE id = %s", (correct_product_id,)
            )
            product = await cur.fetchone()
            correct_product_sku = product["sku"] if product else None

            if found_sku:
                await conn.execute(
                    """
                    INSERT INTO supplier_product (supplier_id, product_id, supplier_sku)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (supplier_id, product_id)
                    DO UPDATE SET supplier_sku = EXCLUDED.supplier_sku
                    """,
                    (supplier["id"], correct_product_id, found_sku),
                )

                # Record a few-shot correction example for future extractions.
                if pc["evidence_text"] and correct_product_sku:
                    await conn.execute(
                        """
                        INSERT INTO supplier_corrections
                          (supplier_id, context, wrong, correct, field)
                        VALUES (%s, %s, %s, %s, 'sku_or_code')
                        """,
                        (
                            supplier["id"],
                            pc["evidence_text"],
                            found_sku,
                            correct_product_sku,
                        ),
                    )

            # Re-point the proposal at the correct line on the same PO.
            cur = await conn.execute(
                "SELECT purchase_order_id FROM purchase_order_line WHERE id = %s",
                (pc["target_record_id"],),
            )
            current_line = await cur.fetchone()

            line_updated = False
            if current_line:
                cur = await conn.execute(
                    """
                    SELECT id, version FROM purchase_order_line
                    WHERE purchase_order_id = %s AND product_id = %s
                    LIMIT 1
                    """,
                    (current_line["purchase_order_id"], correct_product_id),
                )
                correct_line = await cur.fetchone()
                if correct_line:
                    await conn.execute(
                        """
                        UPDATE proposed_changes
                        SET target_record_id = %s, target_record_version = %s
                        WHERE id = %s
                        """,
                        (
                            correct_line["id"],
                            correct_line["version"],
                            proposed_change_id,
                        ),
                    )
                    line_updated = True

    return CorrectSkuResult(
        ok=True, supplier_sku=found_sku, line_updated=line_updated
    )
