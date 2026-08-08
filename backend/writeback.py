"""Writeback: apply an approved proposed_change to the canonical record.

The entire operation runs in a single transaction, and it is the only path
that is allowed to write to `purchase_order_line`.

Two safety properties matter here and are the reason this file is small and
boring on purpose:

  Optimistic locking — `proposed_changes.target_record_version` is the
  version the canonical row had when the change was proposed. If the row has
  moved on since (another change applied first, or an ERP resync), the
  proposal is marked `superseded` and nothing is written. `SELECT … FOR
  UPDATE` on both rows makes concurrent applies queue rather than race.

  Audit log — every canonical write inserts an immutable `audit_log` row
  first, carrying the prior value, the new value, who applied it and the
  proposal that authorised it.
"""

from __future__ import annotations

import logging
from typing import Literal, TypedDict

from psycopg import sql

from backend.db import connection

log = logging.getLogger(__name__)

# Whitelist prevents dynamic SQL injection via field_name values from the DB,
# and pins the cast each text-valued proposal needs on the way in.
WRITABLE_FIELDS: dict[str, tuple[str, str]] = {
    "delivery_date": ("delivery_date", "date"),
    "quantity": ("quantity", "numeric"),
}


class ApplyResult(TypedDict):
    status: Literal["applied", "superseded"]


class ProposedChangeNotFoundError(LookupError):
    pass


class NotApplicableError(ValueError):
    """The proposal cannot be applied as it stands (wrong status, bad target)."""


async def apply_proposed_change(
    proposed_change_id: str,
    applied_by: str = "api",
) -> ApplyResult:
    """Apply one approved change. Returns applied or superseded."""
    async with connection() as conn:
        async with conn.transaction():
            # 1. Load proposed_change — row-lock so concurrent applies queue up.
            cur = await conn.execute(
                "SELECT * FROM proposed_changes WHERE id = %s FOR UPDATE",
                (proposed_change_id,),
            )
            pc = await cur.fetchone()
            if pc is None:
                raise ProposedChangeNotFoundError(
                    f"proposed_change not found: {proposed_change_id}"
                )
            if pc["status"] != "approved":
                raise NotApplicableError(
                    f"cannot apply: status is '{pc['status']}', expected 'approved'"
                )
            if pc["target_table"] != "purchase_order_line":
                raise NotApplicableError(
                    f"unsupported target_table: '{pc['target_table']}'"
                )

            mapping = WRITABLE_FIELDS.get(pc["field_name"])
            if mapping is None:
                raise NotApplicableError(
                    f"unsupported field_name: '{pc['field_name']}'"
                )
            column, cast_type = mapping

            # 2. Load target record — row-lock, then version check.
            cur = await conn.execute(
                "SELECT version FROM purchase_order_line WHERE id = %s FOR UPDATE",
                (pc["target_record_id"],),
            )
            target = await cur.fetchone()
            if target is None:
                raise NotApplicableError(
                    f"target record not found: {pc['target_record_id']}"
                )

            if target["version"] != pc["target_record_version"]:
                await conn.execute(
                    "UPDATE proposed_changes SET status = 'superseded' WHERE id = %s",
                    (proposed_change_id,),
                )
                return ApplyResult(status="superseded")

            # 3. Audit log (immutable — insert only).
            await conn.execute(
                """
                INSERT INTO audit_log
                  (target_table, target_record_id, field_name,
                   prior_value, new_value, applied_by, proposed_change_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    pc["target_table"],
                    pc["target_record_id"],
                    pc["field_name"],
                    pc["old_value"],
                    pc["new_value"],
                    applied_by,
                    proposed_change_id,
                ),
            )

            # 4. Write to canonical record. The increment_version trigger
            #    bumps version, which is what supersedes any sibling proposal
            #    still pointing at the old version.
            await conn.execute(
                sql.SQL(
                    "UPDATE purchase_order_line SET {column} = %s::{cast} WHERE id = %s"
                ).format(
                    column=sql.Identifier(column),
                    cast=sql.SQL(cast_type),
                ),
                (pc["new_value"], pc["target_record_id"]),
            )

            # 5. Mark applied.
            await conn.execute(
                "UPDATE proposed_changes SET status = 'applied' WHERE id = %s",
                (proposed_change_id,),
            )

            return ApplyResult(status="applied")
