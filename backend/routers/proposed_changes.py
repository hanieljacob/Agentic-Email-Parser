"""The review queue: list pending changes, approve them, reject them.

This is the surface the review UI talks to. `GET /` returns everything the
queue needs to render a decision — the change, its confidence, and the
email it came from — in one round trip.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import learning, writeback
from backend.db import connection

router = APIRouter(prefix="/proposed-changes", tags=["proposed-changes"])

REJECTION_REASONS = (
    "wrong_date_format",
    "wrong_sku",
    "not_a_po_update",
    "quantity_is_delta",
    "wrong_po_reference",
    "llm_hallucination",
    "other",
)

RejectionReason = Literal[
    "wrong_date_format",
    "wrong_sku",
    "not_a_po_update",
    "quantity_is_delta",
    "wrong_po_reference",
    "llm_hallucination",
    "other",
]


class ProposedChange(BaseModel):
    id: str
    field_name: str
    old_value: str | None
    new_value: str
    evidence_text: str | None
    extraction_confidence: float
    match_confidence: float
    combined_confidence: float
    status: str
    created_at: datetime
    email_id: str
    sender: str
    subject: str | None
    body_text: str | None
    po_reference: str
    product_sku: str
    product_title: str | None


class ApplyRequest(BaseModel):
    applied_by: str = "api"


class ApproveRequest(BaseModel):
    reviewed_by: str = "reviewer"


class RejectRequest(BaseModel):
    rejection_reason: RejectionReason
    review_notes: str | None = None


class CorrectSkuRequest(BaseModel):
    correct_product_id: str


_LIST_SQL = """
SELECT
  pc.id,
  pc.field_name,
  pc.old_value,
  pc.new_value,
  pc.evidence_text,
  pc.extraction_confidence,
  pc.match_confidence,
  pc.combined_confidence,
  pc.status,
  pc.created_at,
  pc.email_id,
  e.sender,
  e.subject,
  e.body_text,
  po.reference_num AS po_reference,
  p.sku            AS product_sku,
  p.title          AS product_title
FROM proposed_changes pc
JOIN emails e                ON e.id   = pc.email_id
JOIN purchase_order_line pol ON pol.id = pc.target_record_id
JOIN purchase_order po       ON po.id  = pol.purchase_order_id
JOIN product p               ON p.id   = pol.product_id
WHERE pc.status = %s
ORDER BY pc.created_at DESC
"""


def _to_model(row: dict) -> ProposedChange:
    return ProposedChange(
        **{
            **row,
            "id": str(row["id"]),
            "email_id": str(row["email_id"]),
            "extraction_confidence": float(row["extraction_confidence"]),
            "match_confidence": float(row["match_confidence"]),
            "combined_confidence": float(row["combined_confidence"]),
        }
    )


@router.get("", response_model=list[ProposedChange])
async def list_proposed_changes(status: str = "pending") -> list[ProposedChange]:
    """The review queue. Defaults to what a human still has to decide on."""
    async with connection() as conn:
        cur = await conn.execute(_LIST_SQL, (status,))
        return [_to_model(row) for row in await cur.fetchall()]


@router.post("/{change_id}/approve")
async def approve(change_id: str, body: ApproveRequest) -> writeback.ApplyResult:
    """Approve a pending change and write it back in one step.

    Returns `superseded` rather than an error when the target row has moved
    on since the change was proposed — that is a normal outcome, not a
    failure, and the UI reports it as such.
    """
    async with connection() as conn:
        cur = await conn.execute(
            """
            UPDATE proposed_changes
            SET status = 'approved', reviewer_id = %s, reviewed_at = now()
            WHERE id = %s AND status = 'pending'
            RETURNING id
            """,
            (body.reviewed_by, change_id),
        )
        if await cur.fetchone() is None:
            raise HTTPException(
                status_code=422,
                detail=f"proposed_change {change_id} is not pending",
            )

    try:
        return await writeback.apply_proposed_change(change_id, body.reviewed_by)
    except LookupError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.post("/{change_id}/apply")
async def apply(change_id: str, body: ApplyRequest) -> writeback.ApplyResult:
    """Write back a change that is already approved."""
    try:
        return await writeback.apply_proposed_change(change_id, body.applied_by)
    except LookupError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.post("/{change_id}/reject")
async def reject(change_id: str, body: RejectRequest) -> dict[str, bool]:
    """Reject a change. The reason is required and feeds the monitoring view."""
    async with connection() as conn:
        cur = await conn.execute(
            """
            UPDATE proposed_changes
            SET status = 'rejected',
                rejection_reason = %s,
                review_notes = %s,
                reviewed_at = now()
            WHERE id = %s AND status IN ('pending', 'approved')
            RETURNING id
            """,
            (body.rejection_reason, body.review_notes, change_id),
        )
        if await cur.fetchone() is None:
            raise HTTPException(
                status_code=422,
                detail=f"proposed_change {change_id} cannot be rejected",
            )
    return {"ok": True}


@router.post("/{change_id}/correct-sku")
async def correct_sku(
    change_id: str,
    body: CorrectSkuRequest,
) -> learning.CorrectSkuResult:
    try:
        return await learning.correct_sku(change_id, body.correct_product_id)
    except LookupError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
