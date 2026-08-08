"""Sender-address to supplier resolution.

Shared by extract, match and the alias-learning flows, which all need to
answer the same question: which supplier sent this email?
"""

from __future__ import annotations

import re
from typing import Any

from psycopg import AsyncConnection

# supplier.email is the primary signal; supplier_email_aliases holds the
# additional addresses learned through the review UI.
_RESOLVE_SQL = """
    SELECT s.id, s.name, s.llm_notes FROM supplier s WHERE lower(s.email) = %(email)s
    UNION
    SELECT s.id, s.name, s.llm_notes FROM supplier s
    JOIN supplier_email_aliases sea ON sea.supplier_id = s.id
    WHERE lower(sea.email_address) = %(email)s
    LIMIT 1
"""

_ANGLE_ADDR = re.compile(r"<([^>]+)>")


def parse_sender_email(raw: str) -> str:
    """`"Big Supplier" <big@supplier.com>` → `big@supplier.com`."""
    match = _ANGLE_ADDR.search(raw)
    return (match.group(1) if match else raw).lower()


async def resolve_supplier(
    conn: AsyncConnection,
    sender_raw: str,
) -> dict[str, Any] | None:
    """Resolve a raw `From:` header to a supplier row, or None."""
    email = parse_sender_email(sender_raw)
    cur = await conn.execute(_RESOLVE_SQL, {"email": email})
    return await cur.fetchone()
