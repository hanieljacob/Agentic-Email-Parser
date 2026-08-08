"""Extraction step: email_id → context → LLM → extraction_runs row.

The email body, the supplier's known purchase orders, their product-code
aliases and their past corrections are assembled into one prompt. Whatever
the model returns is parsed and validated against `ExtractionOutput` before
it is written. A run is always recorded — on failure with status='error'
and the message — so a bad extraction is a stored fact rather than a crash.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any

from psycopg import AsyncConnection
from pydantic import ValidationError

from backend.config import get_settings
from backend.db import connection
from backend.llm import Content, ExtractionProvider, build_provider
from backend.prompt import SYSTEM_PROMPT, format_context
from backend.schemas import ExtractionOutput
from backend.suppliers import resolve_supplier

log = logging.getLogger(__name__)

_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```")


class ExtractionError(RuntimeError):
    """Raised after the failed run has been recorded."""


class EmailNotFoundError(LookupError):
    pass


def parse_model_content(content: str) -> Any:
    """Parse raw assistant text into JSON, tolerating markdown code fences.

    Returns `{}` when the text is not JSON at all, which validates as an
    empty extraction — see `ExtractionOutput`'s field defaults.
    """
    stripped = content.strip()
    fenced = _FENCE.search(stripped)
    try:
        return json.loads(fenced.group(1) if fenced else stripped)
    except ValueError:
        return {}


# ── context loading ──────────────────────────────────────────────────────────


async def load_context(
    conn: AsyncConnection,
    sender_raw: str,
) -> tuple[dict[str, Any] | None, list[Any], list[Any], list[Any]]:
    """Everything known about the sender, as the prompt needs it."""
    supplier = await resolve_supplier(conn, sender_raw)
    if supplier is None:
        return None, [], [], []

    cur = await conn.execute(
        """
        SELECT po.reference_num,
               po.delivery_date     AS po_delivery_date,
               pol.quantity,
               pol.delivery_date    AS line_delivery_date,
               p.sku,
               p.title              AS product_name
        FROM   purchase_order po
        JOIN   purchase_order_line pol ON pol.purchase_order_id = po.id
        JOIN   product p               ON p.id = pol.product_id
        WHERE  po.supplier_id = %s
        ORDER  BY po.reference_num
        """,
        (supplier["id"],),
    )
    po_rows = await cur.fetchall()

    cur = await conn.execute(
        """
        SELECT sp.supplier_sku, p.sku, p.title AS product_name
        FROM   supplier_product sp
        JOIN   product p ON p.id = sp.product_id
        WHERE  sp.supplier_id = %s AND sp.supplier_sku IS NOT NULL
        """,
        (supplier["id"],),
    )
    alias_rows = await cur.fetchall()

    cur = await conn.execute(
        """
        SELECT context, wrong, correct, field
        FROM supplier_corrections
        WHERE supplier_id = %s
        ORDER BY created_at DESC
        LIMIT 5
        """,
        (supplier["id"],),
    )
    corrections = await cur.fetchall()

    pos = [
        {
            "po_ref": r["reference_num"],
            "po_delivery_date": r["po_delivery_date"].isoformat()
            if r["po_delivery_date"]
            else None,
            "quantity": str(r["quantity"]),
            "line_delivery_date": r["line_delivery_date"].isoformat()
            if r["line_delivery_date"]
            else None,
            "sku": r["sku"],
            "product_name": r["product_name"],
        }
        for r in po_rows
    ]

    aliases = [
        {
            "supplier_sku": r["supplier_sku"],
            "sku": r["sku"],
            "product_name": r["product_name"],
        }
        for r in alias_rows
    ]

    return supplier, pos, aliases, corrections


# ── attachment loading ───────────────────────────────────────────────────────


async def load_attachments(
    conn: AsyncConnection,
    email_id: str,
    attachments_dir: Path,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Split an email's attachments into vision images and extracted text."""
    from backend.attachments import extract_document_text

    cur = await conn.execute(
        """
        SELECT stored_name, original_name, mime_type
        FROM email_attachments
        WHERE email_id = %s
        """,
        (email_id,),
    )
    rows = await cur.fetchall()

    images: list[dict[str, str]] = []
    texts: list[dict[str, str]] = []

    for row in rows:
        try:
            data = (attachments_dir / row["stored_name"]).read_bytes()
        except OSError:
            continue  # skip unreadable files

        if row["mime_type"].startswith("image/"):
            images.append(
                {
                    "original_name": row["original_name"],
                    "mime_type": row["mime_type"],
                    "base64": base64.b64encode(data).decode("ascii"),
                }
            )
        else:
            text = extract_document_text(data, row["mime_type"], row["original_name"])
            if text is not None:
                texts.append(
                    {
                        "original_name": row["original_name"],
                        "mime_type": row["mime_type"],
                        "text": text,
                    }
                )

    return images, texts


# ── core extraction ──────────────────────────────────────────────────────────


async def extract(
    email_id: str,
    provider: ExtractionProvider | None = None,
) -> str:
    """Run extraction for one email. Returns the new extraction_runs id."""
    settings = get_settings()
    provider = provider or build_provider()

    async with connection() as conn:
        cur = await conn.execute(
            "SELECT sender, subject, body_text FROM emails WHERE id = %s",
            (email_id,),
        )
        email_row = await cur.fetchone()
        if email_row is None:
            raise EmailNotFoundError(f"email not found: {email_id}")

        sender = email_row["sender"]
        subject = email_row["subject"]
        body_text = email_row["body_text"]

        supplier, pos, aliases, corrections = await load_context(conn, sender)
        images, texts = await load_attachments(
            conn, email_id, settings.attachments_dir
        )

    # Base text: context + email body + all extractable document text
    base_text = "\n\n".join(
        [
            format_context(supplier, pos, aliases, corrections),
            f"## Email\nFrom: {sender}\nSubject: {subject}\n\n{body_text or '(no body)'}",
            *(f"## Attachment: {t['original_name']}\n{t['text']}" for t in texts),
        ]
    )

    user_content: Content = base_text
    if images:
        user_content = [
            {"type": "text", "text": base_text},
            *(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{img['mime_type']};base64,{img['base64']}"
                    },
                }
                for img in images
            ),
        ]

    llm_output: dict[str, Any] = {}
    status = "success"
    error_message: str | None = None
    # Falls back to the configured provider's name if the call never returns,
    # so a failed run still records what was tried.
    model_version = getattr(provider, "name", "unknown")

    try:
        try:
            completion = await provider.complete(SYSTEM_PROMPT, user_content)
        except Exception as vision_err:
            # Retry text-only if the model doesn't support vision (404 or 500)
            message = str(vision_err)
            if isinstance(user_content, list) and (
                "image" in message or "500" in message
            ):
                log.error("Vision call failed, retrying text-only: %s", message)
                completion = await provider.complete(SYSTEM_PROMPT, base_text)
            else:
                raise

        # Whatever answered — the primary or something down the fallback
        # chain — is what gets recorded against the run.
        model_version = completion.model_version

        try:
            parsed = ExtractionOutput.model_validate(
                parse_model_content(completion.text)
            )
        except ValidationError as err:
            raise ValueError(f"Schema validation failed: {err}") from err
        llm_output = parsed.model_dump()
    except Exception as err:
        status = "error"
        error_message = str(err)

    async with connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                """
                INSERT INTO extraction_runs
                  (email_id, model_version, llm_output, status, error_message)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    email_id,
                    model_version,
                    json.dumps(llm_output),
                    status,
                    error_message,
                ),
            )
            run_row = await cur.fetchone()
            assert run_row is not None
            run_id = str(run_row["id"])

            await conn.execute(
                "UPDATE emails SET status = %s WHERE id = %s",
                ("extracted" if status == "success" else "failed", email_id),
            )

    if status == "error":
        raise ExtractionError(f"Extraction failed: {error_message}")
    return run_id
