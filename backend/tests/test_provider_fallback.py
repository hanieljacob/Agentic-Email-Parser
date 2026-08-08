"""Provider fallback.

A dead key or an unreachable host should degrade to the next provider rather
than fail the email — but a response that fails validation should not, because
that is a modelling problem and retrying it elsewhere would hide a bad prompt.
"""

from __future__ import annotations

import json

import pytest

from backend.config import Settings
from backend.extract import ExtractionError, extract
from backend.llm import (
    AllProvidersFailedError,
    Completion,
    Content,
    FallbackProvider,
    StubProvider,
    build_provider,
)

SUBJECT = "PO-12 delivery update"

VALID_OUTPUT = json.dumps(
    {
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
)


class RecordingProvider:
    """Answers with a fixed payload and counts how often it was asked."""

    def __init__(self, name: str, text: str = VALID_OUTPUT) -> None:
        self.name = name
        self.text = text
        self.calls = 0

    async def complete(self, system_prompt: str, content: Content) -> Completion:
        self.calls += 1
        return Completion(text=self.text, model_version=f"{self.name}:test-model")


class DeadProvider:
    """Stands in for a bad key or an unreachable host."""

    def __init__(self, name: str, message: str = "connection refused") -> None:
        self.name = name
        self.message = message
        self.calls = 0

    async def complete(self, system_prompt: str, content: Content) -> Completion:
        self.calls += 1
        raise RuntimeError(f"{self.name} unreachable: {self.message}")


# ── the chain in isolation ───────────────────────────────────────────────────


async def test_primary_success_never_reaches_the_fallback():
    primary = RecordingProvider("openrouter")
    fallback = RecordingProvider("ollama")

    completion = await FallbackProvider([primary, fallback]).complete("sys", "Subject: x")

    assert completion.model_version == "openrouter:test-model"
    assert primary.calls == 1
    assert fallback.calls == 0


async def test_a_dead_primary_falls_through_to_ollama():
    primary = DeadProvider("openrouter", "401 invalid api key")
    fallback = RecordingProvider("ollama")

    completion = await FallbackProvider([primary, fallback]).complete("sys", "Subject: x")

    assert completion.model_version == "ollama:test-model"
    assert primary.calls == 1
    assert fallback.calls == 1


async def test_the_chain_walks_past_several_dead_providers():
    first = DeadProvider("openrouter")
    second = DeadProvider("ollama")
    third = RecordingProvider("stub")

    completion = await FallbackProvider([first, second, third]).complete(
        "sys", "Subject: x"
    )

    assert completion.model_version == "stub:test-model"
    assert [first.calls, second.calls, third.calls] == [1, 1, 1]


async def test_every_provider_failing_reports_all_of_them():
    chain = FallbackProvider(
        [DeadProvider("openrouter", "401"), DeadProvider("ollama", "connection refused")]
    )

    with pytest.raises(AllProvidersFailedError) as excinfo:
        await chain.complete("sys", "Subject: x")

    message = str(excinfo.value)
    assert "openrouter" in message and "401" in message
    assert "ollama" in message and "connection refused" in message


def test_a_chain_needs_at_least_one_provider():
    with pytest.raises(ValueError):
        FallbackProvider([])


# ── through the pipeline ─────────────────────────────────────────────────────


async def test_extraction_records_the_provider_that_actually_answered(
    seed, make_email, fetch_one
):
    email_id = await make_email(subject=SUBJECT)
    chain = FallbackProvider(
        [DeadProvider("openrouter", "401"), RecordingProvider("ollama")]
    )

    run_id = await extract(email_id, provider=chain)

    run = await fetch_one("SELECT * FROM extraction_runs WHERE id = %s", (run_id,))
    assert run["status"] == "success"
    # Not "openrouter" — the audit trail has to name the model that produced
    # this output, not the one that was configured first.
    assert run["model_version"] == "ollama:test-model"
    assert run["llm_output"]["po_updates"][0]["po_ref"] == "PO-12"


async def test_a_fully_failed_chain_fails_the_record_without_crashing(
    seed, make_email, fetch_one
):
    email_id = await make_email(subject=SUBJECT)
    chain = FallbackProvider([DeadProvider("openrouter"), DeadProvider("ollama")])

    with pytest.raises(ExtractionError):
        await extract(email_id, provider=chain)

    run = await fetch_one(
        "SELECT status, error_message FROM extraction_runs WHERE email_id = %s",
        (email_id,),
    )
    assert run["status"] == "error"
    assert "openrouter" in run["error_message"]
    assert "ollama" in run["error_message"]

    email = await fetch_one("SELECT status FROM emails WHERE id = %s", (email_id,))
    assert email["status"] == "failed"


async def test_invalid_output_is_not_retried_on_the_next_provider(
    seed, make_email, fetch_one
):
    """Availability failures fall through; modelling failures do not."""
    primary = RecordingProvider(
        "openrouter", text='{"po_updates": [{"po_ref": 12, "confidence": 1.0}]}'
    )
    fallback = RecordingProvider("ollama")
    email_id = await make_email(subject=SUBJECT)

    with pytest.raises(ExtractionError):
        await extract(email_id, provider=FallbackProvider([primary, fallback]))

    assert primary.calls == 1
    assert fallback.calls == 0  # the answer was bad, not missing

    run = await fetch_one(
        "SELECT status, error_message FROM extraction_runs WHERE email_id = %s",
        (email_id,),
    )
    assert run["status"] == "error"
    assert "Schema validation failed" in run["error_message"]


# ── wiring from settings ─────────────────────────────────────────────────────


def _settings(**overrides) -> Settings:
    # _env_file=None so a developer's .env cannot change the result.
    return Settings(_env_file=None, **overrides)


def test_no_fallback_configured_yields_a_bare_provider(monkeypatch):
    monkeypatch.setattr(
        "backend.config.get_settings", lambda: _settings(llm_provider="stub")
    )

    provider = build_provider()

    assert isinstance(provider, StubProvider)


def test_fallback_setting_builds_a_chain(monkeypatch):
    monkeypatch.setattr(
        "backend.config.get_settings",
        lambda: _settings(
            llm_provider="openrouter",
            openrouter_api_key="sk-test",
            llm_fallback="ollama,stub",
        ),
    )

    provider = build_provider()

    assert isinstance(provider, FallbackProvider)
    assert [p.name for p in provider.providers] == ["openrouter", "ollama", "stub"]


def test_the_primary_is_not_duplicated_by_the_fallback_list(monkeypatch):
    monkeypatch.setattr(
        "backend.config.get_settings",
        lambda: _settings(llm_provider="ollama", llm_fallback="ollama,stub"),
    )

    provider = build_provider()

    assert [p.name for p in provider.providers] == ["ollama", "stub"]


def test_a_misconfigured_fallback_is_skipped_not_fatal(monkeypatch):
    """OpenRouter with no key must not break an otherwise working chain."""
    monkeypatch.setattr(
        "backend.config.get_settings",
        lambda: _settings(
            llm_provider="ollama", llm_fallback="openrouter", openrouter_api_key=None
        ),
    )

    provider = build_provider()

    assert provider.name == "ollama"


@pytest.mark.parametrize(
    ("fallback", "expected"),
    [
        ("", []),
        ("ollama", ["ollama"]),
        ("ollama,stub", ["ollama", "stub"]),
        (" ollama , stub ", ["ollama", "stub"]),
    ],
)
def test_fallback_setting_parsing(fallback, expected):
    assert _settings(llm_fallback=fallback).fallback_providers == expected
