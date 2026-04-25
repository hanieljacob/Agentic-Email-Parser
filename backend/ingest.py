#!/usr/bin/env python3
"""Email ingestion service.

Entry points:
  HTTP:  uvicorn ingest:app  →  POST /emails  (raw RFC 822 body)
  CLI:   python ingest.py <path/to/file.eml>
"""

import argparse
import email as _email
import email.policy
import email.utils
import hashlib
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
import psycopg
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

# Load .env from the project root (one level up from backend/)
load_dotenv(Path(__file__).parent.parent / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]
ATTACHMENTS_DIR = Path(os.environ.get("ATTACHMENTS_DIR", "./attachments"))

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST"],
    allow_headers=["*"],
)


def _plain_text(msg) -> str:
    """Prefer text/plain; fall back to tag-stripped text/html."""
    plain = html = None
    for part in msg.walk():
        ct = part.get_content_type()
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        text = payload.decode(errors="replace")
        if ct == "text/plain" and plain is None:
            plain = text
        elif ct == "text/html" and html is None:
            html = text
    if plain is not None:
        return plain
    if html is not None:
        return re.sub(r"<[^>]+>", " ", html)
    return ""


def _save_attachments(msg) -> list[tuple[str, str, str]]:
    """Save attachments to disk; return list of (stored_name, original_name, mime_type)."""
    ATTACHMENTS_DIR.mkdir(exist_ok=True)
    saved = []
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
        dest = ATTACHMENTS_DIR / stored_name
        if not dest.exists():
            dest.write_bytes(payload)
        mime_type = part.get_content_type() or "application/octet-stream"
        saved.append((stored_name, original_name, mime_type))
    return saved


def ingest(raw: bytes) -> str:
    """Parse RFC 822 bytes, persist to DB (idempotent on content_hash), return uuid."""
    content_hash = hashlib.sha256(raw).hexdigest()
    msg = _email.message_from_bytes(raw, policy=_email.policy.compat32)

    message_id = (msg.get("Message-ID") or f"<{content_hash}@local>").strip()
    sender = msg.get("From", "")
    subject = msg.get("Subject", "")

    date_str = msg.get("Date")
    try:
        received_at = _email.utils.parsedate_to_datetime(date_str) if date_str else None
    except Exception:
        received_at = None

    body_text = _plain_text(msg)
    attachments = _save_attachments(msg)

    with psycopg.connect(DATABASE_URL) as conn:
        existing = conn.execute(
            "SELECT id FROM emails WHERE content_hash = %s", (content_hash,)
        ).fetchone()
        if existing:
            return str(existing[0])

        row = conn.execute(
            """
            INSERT INTO emails
              (message_id, sender, subject, received_at, body_text, content_hash, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'ingested')
            RETURNING id
            """,
            (message_id, sender, subject, received_at, body_text, content_hash),
        ).fetchone()
        email_id = str(row[0])

        for stored_name, original_name, mime_type in attachments:
            conn.execute(
                """
                INSERT INTO email_attachments (email_id, stored_name, original_name, mime_type)
                VALUES (%s, %s, %s, %s)
                """,
                (email_id, stored_name, original_name, mime_type),
            )

        conn.commit()
        return email_id


@app.post("/emails")
async def http_ingest(request: Request) -> Response:
    email_id = ingest(await request.body())
    return Response(content=email_id, media_type="text/plain", status_code=200)


def cli_main() -> None:
    ap = argparse.ArgumentParser(description="Ingest an .eml file into the database.")
    ap.add_argument("path", help="Path to .eml file")
    args = ap.parse_args()
    print(ingest(Path(args.path).read_bytes()))


if __name__ == "__main__":
    # If first arg looks like a file path, run CLI mode; otherwise serve HTTP.
    if sys.argv[1:] and not sys.argv[1].startswith("--"):
        cli_main()
    else:
        uvicorn.run("ingest:app", host="0.0.0.0", port=8000)
