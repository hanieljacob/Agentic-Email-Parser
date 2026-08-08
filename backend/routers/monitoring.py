"""Operational monitoring — the four queries behind the /monitoring page."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from backend.db import connection

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


class StatusCount(BaseModel):
    status: str
    count: int


class StuckEmail(BaseModel):
    id: str
    sender: str
    subject: str | None
    status: str
    received_at: datetime
    attempt_count: int


class RejectionPattern(BaseModel):
    supplier_name: str
    has_notes: bool
    rejection_reason: str
    count: int


class ChangesSummary(BaseModel):
    pending: int
    total_applied: int
    total_rejected: int
    avg_confidence: str | None


class MonitoringData(BaseModel):
    status_counts: list[StatusCount]
    stuck_emails: list[StuckEmail]
    rejections: list[RejectionPattern]
    changes_summary: ChangesSummary


@router.get("", response_model=MonitoringData)
async def get_monitoring_data() -> MonitoringData:
    async with connection() as conn:
        cur = await conn.execute(
            "SELECT status, count FROM pipeline_status ORDER BY count DESC"
        )
        status_counts = await cur.fetchall()

        cur = await conn.execute(
            """
            SELECT
              e.id, e.sender, e.subject, e.status, e.received_at,
              count(er.id)::integer AS attempt_count
            FROM emails e
            LEFT JOIN extraction_runs er ON er.email_id = e.id
            WHERE e.status IN ('ingested', 'failed')
              AND e.received_at < now() - interval '1 hour'
            GROUP BY e.id, e.sender, e.subject, e.status, e.received_at
            ORDER BY e.received_at ASC
            LIMIT 50
            """
        )
        stuck = await cur.fetchall()

        cur = await conn.execute(
            """
            SELECT supplier_name, has_notes, rejection_reason, count
            FROM rejection_patterns
            ORDER BY supplier_name, count DESC
            """
        )
        rejections = await cur.fetchall()

        cur = await conn.execute(
            """
            SELECT
              count(*) FILTER (WHERE status = 'pending')::integer  AS pending,
              count(*) FILTER (WHERE status = 'applied')::integer  AS total_applied,
              count(*) FILTER (WHERE status = 'rejected')::integer AS total_rejected,
              round(avg(combined_confidence) FILTER (WHERE status = 'applied'), 3)::text
                AS avg_confidence
            FROM proposed_changes
            """
        )
        summary = await cur.fetchone()

    return MonitoringData(
        status_counts=[StatusCount(**row) for row in status_counts],
        stuck_emails=[
            StuckEmail(**{**row, "id": str(row["id"])}) for row in stuck
        ],
        rejections=[RejectionPattern(**row) for row in rejections],
        changes_summary=ChangesSummary(
            **(
                summary
                or {
                    "pending": 0,
                    "total_applied": 0,
                    "total_rejected": 0,
                    "avg_confidence": None,
                }
            )
        ),
    )
