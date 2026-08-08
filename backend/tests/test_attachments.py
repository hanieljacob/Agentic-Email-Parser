"""Attachment text extraction.

The contract that matters is the silent-skip one: an unsupported format or a
corrupt file returns None and the attachment is left out of the prompt. A bad
attachment must never fail an extraction.

Fixtures are generated in-memory rather than committed, so the test asserts
against the libraries actually installed.
"""

from __future__ import annotations

import io

import pytest

from backend.attachments import extract_document_text

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _xlsx(rows: list[list], sheet_title: str = "Orders") -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _docx(paragraphs: list[str]) -> bytes:
    from docx import Document

    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _pdf_without_text_layer() -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ── spreadsheets ─────────────────────────────────────────────────────────────


def test_xlsx_renders_each_sheet_as_csv():
    data = _xlsx([["sku", "qty"], ["SKU-2", 200], ["SKU-1-3", 15000]])

    text = extract_document_text(data, XLSX_MIME, "order.xlsx")

    assert text is not None
    assert "[Sheet: Orders]" in text
    assert "sku,qty" in text
    assert "SKU-2,200" in text


def test_xlsx_integers_do_not_render_as_floats():
    """openpyxl hands back 200 as 200.0; a prompt should not see that."""
    text = extract_document_text(_xlsx([["qty"], [200]]), XLSX_MIME, "q.xlsx")

    assert "200" in text
    assert "200.0" not in text


def test_xlsx_blank_cells_render_as_empty_fields():
    text = extract_document_text(
        _xlsx([["a", "b", "c"], [1, None, 3]]), XLSX_MIME, "gaps.xlsx"
    )

    assert "1,,3" in text


def test_legacy_xls_mime_is_accepted_but_unreadable():
    """openpyxl cannot read real .xls; the format is claimed, so it must not raise."""
    assert extract_document_text(b"\xd0\xcf\x11\xe0garbage", "application/vnd.ms-excel", "old.xls") is None


# ── word ─────────────────────────────────────────────────────────────────────


def test_docx_returns_paragraph_text():
    data = _docx(["Delivery for SKU-2 is now 3 February 2026.", "Regards,"])

    text = extract_document_text(data, DOCX_MIME, "note.docx")

    assert text is not None
    assert "Delivery for SKU-2" in text
    assert "Regards," in text


def test_empty_docx_is_skipped():
    assert extract_document_text(_docx([]), DOCX_MIME, "empty.docx") is None


# ── pdf ──────────────────────────────────────────────────────────────────────


def test_pdf_without_a_text_layer_returns_a_reviewer_note():
    text = extract_document_text(_pdf_without_text_layer(), "application/pdf", "scan.pdf")

    assert text is not None
    assert "Scanned PDF" in text
    # The filename is in the note so a reviewer knows which file to open.
    assert "scan.pdf" in text


# ── plain text ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("mime", ["text/plain", "text/csv"])
def test_plain_formats_are_decoded(mime):
    assert extract_document_text(b"  PO-12,SKU-2,200  ", mime, "f.txt") == "PO-12,SKU-2,200"


def test_undecodable_bytes_do_not_raise():
    text = extract_document_text(b"\xff\xfe\x00bad", "text/plain", "f.txt")

    assert text is None or isinstance(text, str)


def test_empty_text_file_is_skipped():
    assert extract_document_text(b"   \n  ", "text/plain", "blank.txt") is None


# ── the silent-skip contract ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "mime",
    ["application/zip", "video/mp4", "application/octet-stream", "image/png"],
)
def test_unsupported_formats_are_skipped(mime):
    assert extract_document_text(b"anything", mime, "f.bin") is None


@pytest.mark.parametrize(
    ("mime", "name"),
    [
        ("application/pdf", "broken.pdf"),
        (XLSX_MIME, "broken.xlsx"),
        (DOCX_MIME, "broken.docx"),
    ],
)
def test_corrupt_files_are_skipped_rather_than_raising(mime, name):
    """A truncated attachment must not take the extraction down with it."""
    assert extract_document_text(b"not really a document", mime, name) is None
