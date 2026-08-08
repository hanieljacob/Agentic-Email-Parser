"""Extraction output validation.

These tests are about the boundary between the model and the database: what
the pipeline accepts, what it rejects, and what it records when it rejects.
They do not test the model — every response is supplied by the offline stub.
"""

from __future__ import annotations

import json

import pytest

from backend.extract import ExtractionError, extract, parse_model_content
from backend.llm import Content, StubProvider

SUBJECT = "PO-12 delivery update"

VALID_OUTPUT = {
    "po_updates": [
        {
            "po_ref": "PO-12",
            "source": "body",
            "evidence": "delivery now 3 February",
            "confidence": 1.0,
            "line_updates": [
                {
                    "sku_or_code": "SKU-2",
                    "field": "delivery_date",
                    "new_value": "2026-02-03",
                    "evidence": "delivery now 3 February",
                    "confidence": 1.0,
                }
            ],
        }
    ],
    "unmatched_mentions": [],
}


def stub(raw: str) -> StubProvider:
    return StubProvider({SUBJECT: raw})


class BrokenProvider:
    """A provider whose upstream call fails outright."""

    name = "broken"
    model_version = "broken"

    async def complete(self, system_prompt: str, content: Content) -> str:
        raise RuntimeError("OpenRouter 401: invalid api key")


# ── parse_model_content ──────────────────────────────────────────────────────


def test_parses_bare_json():
    assert parse_model_content('{"a": 1}') == {"a": 1}


def test_strips_markdown_code_fence():
    raw = '```json\n{"a": 1}\n```'
    assert parse_model_content(raw) == {"a": 1}


def test_strips_unlabelled_code_fence():
    assert parse_model_content('```\n{"a": 1}\n```') == {"a": 1}


def test_non_json_becomes_empty_object():
    assert parse_model_content("I'm sorry, I can't help with that.") == {}


# ── the success path ─────────────────────────────────────────────────────────


async def test_valid_output_is_stored_and_email_marked_extracted(
    seed, make_email, fetch_one
):
    email_id = await make_email(subject=SUBJECT)

    run_id = await extract(email_id, provider=stub(json.dumps(VALID_OUTPUT)))

    run = await fetch_one("SELECT * FROM extraction_runs WHERE id = %s", (run_id,))
    assert run["status"] == "success"
    assert run["error_message"] is None
    assert run["llm_output"] == VALID_OUTPUT
    assert run["model_version"] == "offline-stub"

    email = await fetch_one("SELECT status FROM emails WHERE id = %s", (email_id,))
    assert email["status"] == "extracted"


async def test_fenced_output_is_unwrapped_before_validation(seed, make_email, fetch_one):
    email_id = await make_email(subject=SUBJECT)
    fenced = f"```json\n{json.dumps(VALID_OUTPUT)}\n```"

    run_id = await extract(email_id, provider=stub(fenced))

    run = await fetch_one("SELECT * FROM extraction_runs WHERE id = %s", (run_id,))
    assert run["status"] == "success"
    assert run["llm_output"]["po_updates"][0]["po_ref"] == "PO-12"


async def test_optional_fields_take_their_defaults(seed, make_email, fetch_one):
    """`source` and `line_updates` are omitted; both must be filled in."""
    email_id = await make_email(subject=SUBJECT)
    minimal = {
        "po_updates": [
            {"po_ref": "PO-12", "evidence": "noted", "confidence": 0.8}
        ]
    }

    run_id = await extract(email_id, provider=stub(json.dumps(minimal)))

    run = await fetch_one("SELECT llm_output FROM extraction_runs WHERE id = %s", (run_id,))
    po_update = run["llm_output"]["po_updates"][0]
    assert po_update["source"] == "body"
    assert po_update["line_updates"] == []
    assert run["llm_output"]["unmatched_mentions"] == []


# ── the invalid output path ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("description", "payload"),
    [
        (
            "confidence above 1.0",
            {
                "po_updates": [
                    {
                        "po_ref": "PO-12",
                        "evidence": "e",
                        "confidence": 5.0,
                        "line_updates": [],
                    }
                ]
            },
        ),
        (
            "unknown field name",
            {
                "po_updates": [
                    {
                        "po_ref": "PO-12",
                        "evidence": "e",
                        "confidence": 1.0,
                        "line_updates": [
                            {
                                "sku_or_code": "SKU-2",
                                "field": "unit_price",
                                "new_value": "9",
                                "evidence": "e",
                                "confidence": 1.0,
                            }
                        ],
                    }
                ]
            },
        ),
        (
            "missing required key",
            {"po_updates": [{"po_ref": "PO-12", "confidence": 1.0}]},
        ),
        (
            "wrong type for po_ref",
            {
                "po_updates": [
                    {"po_ref": 12, "evidence": "e", "confidence": 1.0}
                ]
            },
        ),
    ],
)
async def test_schema_violation_fails_the_record(
    seed, make_email, fetch_one, description, payload
):
    """Invalid output is recorded as an error run — nothing reaches canonical data."""
    email_id = await make_email(subject=SUBJECT)

    with pytest.raises(ExtractionError):
        await extract(email_id, provider=stub(json.dumps(payload)))

    run = await fetch_one(
        "SELECT * FROM extraction_runs WHERE email_id = %s", (email_id,)
    )
    assert run["status"] == "error", description
    assert "Schema validation failed" in run["error_message"]

    email = await fetch_one("SELECT status FROM emails WHERE id = %s", (email_id,))
    assert email["status"] == "failed"

    assert await fetch_one("SELECT * FROM proposed_changes") is None


async def test_provider_failure_is_recorded_not_raised_raw(seed, make_email, fetch_one):
    email_id = await make_email(subject=SUBJECT)

    with pytest.raises(ExtractionError):
        await extract(email_id, provider=BrokenProvider())

    run = await fetch_one(
        "SELECT status, error_message FROM extraction_runs WHERE email_id = %s",
        (email_id,),
    )
    assert run["status"] == "error"
    assert "invalid api key" in run["error_message"]


async def test_a_failed_extraction_does_not_break_the_next_one(
    seed, make_email, fetch_one
):
    """The pipeline keeps running after a bad record — no poisoned state."""
    bad_email = await make_email(subject=SUBJECT)
    with pytest.raises(ExtractionError):
        await extract(bad_email, provider=stub('{"po_updates": [{"po_ref": 1}]}'))

    good_email = await make_email(subject=SUBJECT)
    run_id = await extract(good_email, provider=stub(json.dumps(VALID_OUTPUT)))

    run = await fetch_one("SELECT status FROM extraction_runs WHERE id = %s", (run_id,))
    assert run["status"] == "success"


async def test_unparseable_output_is_recorded_as_an_empty_extraction(
    seed, make_email, fetch_one
):
    """Documents a deliberate carry-over from the TypeScript implementation.

    Text that is not JSON at all parses to `{}`, which is *valid* against the
    schema because every top-level field has a default. So a refusal or a
    stray apology is stored as a successful run with zero updates rather than
    as an error. Nothing is written to canonical data either way, but the run
    is not flagged for retry.
    """
    email_id = await make_email(subject=SUBJECT)

    run_id = await extract(email_id, provider=stub("I'm sorry, I can't help with that."))

    run = await fetch_one("SELECT * FROM extraction_runs WHERE id = %s", (run_id,))
    assert run["status"] == "success"
    assert run["llm_output"] == {"po_updates": [], "unmatched_mentions": []}


# ── stub determinism ─────────────────────────────────────────────────────────


async def test_stub_returns_empty_extraction_for_unknown_subject(
    seed, make_email, fetch_one
):
    email_id = await make_email(subject="Something the stub has never seen")

    run_id = await extract(email_id, provider=stub(json.dumps(VALID_OUTPUT)))

    run = await fetch_one("SELECT llm_output FROM extraction_runs WHERE id = %s", (run_id,))
    assert run["llm_output"]["po_updates"] == []
