"""LLM providers for the extraction step.

Three implementations behind one protocol, plus a wrapper that chains them:

  OpenAICompatibleProvider — OpenRouter and Ollama both speak the OpenAI API,
                             so they are the same class with different hosts.
                             The JSON schema derived from the Pydantic models
                             is sent as `response_format`, constraining the
                             model at generation time as well as validating
                             afterwards.

  StubProvider             — deterministic, offline, no API key. Canned
                             responses keyed by email subject. This is what
                             makes `pnpm seed` reproduce the same review queue
                             on every machine, and what the tests run against.

  FallbackProvider         — tries each provider in order and returns the
                             first success, so a dead key or an unreachable
                             host degrades to the next one instead of failing
                             the email.

No provider validates: they return raw assistant text and `extract()` is
responsible for parsing and validating it.
"""

from __future__ import annotations

import email as email_lib
import email.policy
import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from backend.schemas import extraction_json_schema

log = logging.getLogger(__name__)

# A user message is either plain text or a list of OpenAI content parts
# (text + image_url) when the email carried image attachments.
Content = str | list[dict[str, Any]]

EMPTY_EXTRACTION = '{"po_updates": [], "unmatched_mentions": []}'


@dataclass(frozen=True)
class Completion:
    """Raw model output, plus which model actually produced it.

    `model_version` travels with the response rather than being read off the
    provider afterwards, because with a fallback chain the provider that
    answered is not known until it has. Reading it from shared state would
    also race between concurrent extractions.
    """

    text: str
    model_version: str


@runtime_checkable
class ExtractionProvider(Protocol):
    """Anything that can turn a prompt into raw model output."""

    name: str

    async def complete(self, system_prompt: str, content: Content) -> Completion: ...


class AllProvidersFailedError(RuntimeError):
    """Every provider in the chain raised."""


def text_of(content: Content) -> str:
    """The text portion of a user message, ignoring any image parts."""
    if isinstance(content, str):
        return content
    return "\n".join(
        part.get("text", "") for part in content if part.get("type") == "text"
    )


def subject_of(content: Content) -> str | None:
    """Pull the `Subject:` line out of an assembled prompt."""
    match = re.search(r"^Subject: (.*)$", text_of(content), re.MULTILINE)
    return match.group(1).strip() if match else None


# ── OpenAI-compatible hosts ──────────────────────────────────────────────────


class OpenAICompatibleProvider:
    """OpenRouter, Ollama, or anything else speaking the OpenAI chat API."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        structured_output: bool = True,
    ) -> None:
        from openai import AsyncOpenAI

        self.name = name
        self.model = model
        # Not every local model supports json_schema response_format. When it
        # is off the schema still lives in the system prompt, and the response
        # is validated either way.
        self.structured_output = structured_output
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    async def complete(self, system_prompt: str, content: Content) -> Completion:
        from openai import APIError

        kwargs: dict[str, Any] = {}
        if self.structured_output:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "po_extraction",
                    "strict": True,
                    "schema": extraction_json_schema(),
                },
            }

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},  # type: ignore[dict-item]
                ],
                **kwargs,
            )
        except APIError as err:
            # Surface the full error body — the status alone is rarely enough
            # to tell a bad key from an unsupported model.
            detail = json.dumps(getattr(err, "body", None) or str(err))
            status = getattr(err, "status_code", "?")
            log.error("%s error %s (model=%s): %s", self.name, status, self.model, detail)
            raise RuntimeError(f"{self.name} {status}: {detail}") from err
        except Exception as err:  # connection refused, DNS, timeout
            log.error("%s unreachable (model=%s): %s", self.name, self.model, err)
            raise RuntimeError(f"{self.name} unreachable: {err}") from err

        return Completion(
            text=response.choices[0].message.content or "",
            model_version=f"{self.name}:{self.model}",
        )


def openrouter_provider(
    api_key: str, model: str, base_url: str
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        name="openrouter", base_url=base_url, api_key=api_key, model=model
    )


def ollama_provider(
    base_url: str, model: str, structured_output: bool = True
) -> OpenAICompatibleProvider:
    # Ollama ignores the key but the OpenAI client insists on one.
    return OpenAICompatibleProvider(
        name="ollama",
        base_url=base_url,
        api_key="ollama",
        model=model,
        structured_output=structured_output,
    )


# ── offline stub ─────────────────────────────────────────────────────────────


class StubProvider:
    """Deterministic offline provider.

    Responses are keyed by the email's Subject header. An email with no
    canned response yields an empty extraction, which is the same thing the
    real pipeline does for a message containing no PO updates.
    """

    name = "stub"
    model_version = "offline-stub"

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self.responses = responses or {}

    @classmethod
    def from_fixtures(cls, fixtures_dir: Path) -> StubProvider:
        """Load `<name>.eml` + `<name>.json` pairs from a directory.

        The `.eml` supplies the subject key; the `.json` alongside it is the
        canned model output, stored verbatim so a fixture can deliberately
        contain malformed output.
        """
        responses: dict[str, str] = {}
        if not fixtures_dir.is_dir():
            return cls(responses)

        for eml_path in sorted(fixtures_dir.glob("*.eml")):
            response_path = eml_path.with_suffix(".json")
            if not response_path.exists():
                continue
            msg = email_lib.message_from_bytes(
                eml_path.read_bytes(), policy=email_lib.policy.compat32
            )
            subject = str(msg.get("Subject", "")).strip()
            if subject:
                responses[subject] = response_path.read_text(encoding="utf-8")

        return cls(responses)

    async def complete(self, system_prompt: str, content: Content) -> Completion:
        subject = subject_of(content)
        text = self.responses.get(subject or "", EMPTY_EXTRACTION)
        return Completion(text=text, model_version=self.model_version)


# ── fallback chain ───────────────────────────────────────────────────────────


class FallbackProvider:
    """Try each provider in order; return the first that answers.

    Falls back on any raised error — a dead key, a rate limit, an unreachable
    host. It deliberately does NOT fall back on output the model returns but
    that fails validation: that is a modelling problem, not an availability
    one, and retrying it elsewhere would hide a bad prompt behind a second
    opinion. Validation happens in `extract()`, after this returns.
    """

    def __init__(self, providers: Sequence[ExtractionProvider]) -> None:
        if not providers:
            raise ValueError("FallbackProvider needs at least one provider")
        self.providers = list(providers)
        self.name = " → ".join(p.name for p in self.providers)

    async def complete(self, system_prompt: str, content: Content) -> Completion:
        failures: list[str] = []

        for index, provider in enumerate(self.providers):
            try:
                completion = await provider.complete(system_prompt, content)
            except Exception as err:
                failures.append(f"{provider.name}: {err}")
                remaining = self.providers[index + 1 :]
                if remaining:
                    log.warning(
                        "provider %s failed, falling back to %s: %s",
                        provider.name,
                        remaining[0].name,
                        err,
                    )
                continue

            if index > 0:
                log.info("extraction served by fallback provider %s", provider.name)
            return completion

        raise AllProvidersFailedError(
            "all providers failed — " + "; ".join(failures)
        )


# ── selection ────────────────────────────────────────────────────────────────


def _build_one(name: str) -> ExtractionProvider:
    from backend.config import PROJECT_ROOT, get_settings

    settings = get_settings()

    if name == "openrouter":
        if not settings.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY is not set")
        return openrouter_provider(
            api_key=settings.openrouter_api_key,
            model=settings.model_name,
            base_url=settings.openrouter_base_url,
        )

    if name == "ollama":
        return ollama_provider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            structured_output=settings.ollama_structured_output,
        )

    if name == "stub":
        return StubProvider.from_fixtures(PROJECT_ROOT / "backend" / "fixtures")

    raise ValueError(f"unknown provider: {name}")


def build_provider() -> ExtractionProvider:
    """Construct the provider chain named by settings.

    `LLM_PROVIDER=auto` (the default) picks OpenRouter when a key is present
    and the offline stub otherwise, so a fresh clone with no `.env` still runs
    the full pipeline end to end. `LLM_FALLBACK` appends providers to try when
    the primary fails.
    """
    from backend.config import get_settings

    settings = get_settings()

    chain: list[str] = [settings.resolved_provider]
    for name in settings.fallback_providers:
        if name not in chain:
            chain.append(name)

    providers: list[ExtractionProvider] = []
    for name in chain:
        try:
            providers.append(_build_one(name))
        except ValueError as err:
            # A misconfigured fallback should not stop the primary working.
            log.warning("skipping provider %s: %s", name, err)

    if not providers:
        raise ValueError(f"no usable extraction provider in chain: {chain}")

    return providers[0] if len(providers) == 1 else FallbackProvider(providers)
