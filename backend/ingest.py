"""Email ingestion: raw RFC 822 bytes → an `emails` row (+ attachments).

Idempotent on the SHA-256 of the raw bytes, so the same physical email can
never be ingested twice. Attachments are written to ATTACHMENTS_DIR under
their content hash and recorded in `email_attachments`.
"""

from __future__ import annotations

import email as email_lib
import email.policy
import email.utils
import hashlib
import re
import sys
from datetime import datetime
from email.message import Message
from pathlib import Path

from backend.config import get_settings
from backend.db import connection

_TAG = re.compile(r"<[^>]+>")


def plain_text(msg: Message) -> str:
    """Prefer text/plain; fall back to tag-stripped text/html."""
    plain: str | None = None
    html: str | None = None
    for part in msg.walk():
        content_type = part.get_content_type()
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        text = payload.decode(errors="replace")
        if content_type == "text/plain" and plain is None:
            plain = text
        elif content_type == "text/html" and html is None:
            html = text
    if plain is not None:
        return plain
    if html is not None:
        return _TAG.sub(" ", html)
    return ""


def save_attachments(msg: Message, attachments_dir: Path) -> list[tuple[str, str, str]]:
    """Write attachments to disk; return (stored_name, original_name, mime_type)."""
    attachments_dir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[str, str, str]] = []
    for part in msg.walk():
        if part.get_content_disposition() != "attachment":
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        content_hash = hashlib.sha256(payload).hexdigest()
        original_name = part.get_filename() or "attachment"
        suffix = Path(original_name).suffix or ".bin"
        stored_name = f"{content_hash}{suffix}"
        destination = attachments_dir / stored_name
        if not destination.exists():
            destination.write_bytes(payload)
        mime_type = part.get_content_type() or "application/octet-stream"
        saved.append((stored_name, original_name, mime_type))
    return saved


def _received_at(msg: Message) -> datetime | None:
    date_str = msg.get("Date")
    if not date_str:
        return None
    try:
        return email.utils.parsedate_to_datetime(date_str)
    except (TypeError, ValueError):
        return None


async def ingest(raw: bytes) -> tuple[str, bool]:
    """Parse and persist one email. Returns (email_id, is_new)."""
    settings = get_settings()
    content_hash = hashlib.sha256(raw).hexdigest()
    msg = email_lib.message_from_bytes(raw, policy=email_lib.policy.compat32)

    message_id = str(msg.get("Message-ID") or f"<{content_hash}@local>").strip()
    sender = str(msg.get("From", ""))
    subject = str(msg.get("Subject", ""))
    body_text = plain_text(msg)
    attachments = save_attachments(msg, settings.attachments_dir)

    async with connection() as conn:
        cur = await conn.execute(
            "SELECT id FROM emails WHERE content_hash = %s", (content_hash,)
        )
        existing = await cur.fetchone()
        if existing:
            return str(existing["id"]), False

        async with conn.transaction():
            cur = await conn.execute(
                """
                INSERT INTO emails
                  (message_id, sender, subject, received_at, body_text, content_hash, status)
                VALUES (%s, %s, %s, COALESCE(%s, now()), %s, %s, 'ingested')
                RETURNING id
                """,
                (
                    message_id,
                    sender,
                    subject,
                    _received_at(msg),
                    body_text,
                    content_hash,
                ),
            )
            row = await cur.fetchone()
            assert row is not None
            email_id = str(row["id"])

            for stored_name, original_name, mime_type in attachments:
                await conn.execute(
                    """
                    INSERT INTO email_attachments
                      (email_id, stored_name, original_name, mime_type)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (email_id, stored_name, original_name, mime_type),
                )

    return email_id, True


async def _cli() -> None:
    """python -m backend.ingest <path/to/file.eml>"""
    from backend.db import close_pool, open_pool

    if len(sys.argv) < 2:
        print("usage: python -m backend.ingest <path/to/file.eml>", file=sys.stderr)
        raise SystemExit(1)

    await open_pool()
    try:
        email_id, is_new = await ingest(Path(sys.argv[1]).read_bytes())
        print(email_id)
        if not is_new:
            print("(duplicate — already ingested)", file=sys.stderr)
    finally:
        await close_pool()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_cli())
