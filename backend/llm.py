"""LLM providers for the extraction step.

Two implementations behind one protocol:

  OpenRouterProvider — the real thing, via the OpenAI-compatible client.
                       The JSON schema derived from the Pydantic models is
                       sent as `response_format` so the model is constrained
                       at generation time as well as validated afterwards.

  StubProvider       — deterministic, offline, no API key. Returns canned
                       responses keyed by email subject. This is what makes
                       `pnpm seed` reproduce the same review queue on every
                       machine, and what the tests run against.

Neither provider validates: both return raw assistant text, and `extract()`
is responsible for parsing and validating it.
"""

from __future__ import annotations

import email as email_lib
import email.policy
import json
import logging
import re
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from backend.schemas import extraction_json_schema

log = logging.getLogger(__name__)

# A user message is either plain text or a list of OpenAI content parts
# (text + image_url) when the email carried image attachments.
Content = str | list[dict[str, Any]]

EMPTY_EXTRACTION = '{"po_updates": [], "unmatched_mentions": []}'


@runtime_checkable
class ExtractionProvider(Protocol):
    """Anything that can turn a prompt into raw model output."""

    name: str
    #: Recorded on the extraction_runs row so a stubbed run is never
    #: mistaken for a real model's output when reading history back.
    model_version: str

    async def complete(self, system_prompt: str, content: Content) -> str: ...


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


# ── OpenRouter ───────────────────────────────────────────────────────────────


class OpenRouterProvider:
    name = "openrouter"

    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        from openai import AsyncOpenAI

        self.model = model
        self.model_version = model
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    async def complete(self, system_prompt: str, content: Content) -> str:
        from openai import APIError

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},  # type: ignore[dict-item]
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "po_extraction",
                        "strict": True,
                        "schema": extraction_json_schema(),
                    },
                },
            )
        except APIError as err:
            # Surface the full OpenRouter error body — the status alone is
            # rarely enough to tell a bad key from an unsupported model.
            detail = json.dumps(getattr(err, "body", None) or str(err))
            log.error(
                "OpenRouter error %s (model=%s): %s",
                getattr(err, "status_code", "?"),
                self.model,
                detail,
            )
            raise RuntimeError(
                f"OpenRouter {getattr(err, 'status_code', '?')}: {detail}"
            ) from err

        return response.choices[0].message.content or ""


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

    async def complete(self, system_prompt: str, content: Content) -> str:
        subject = subject_of(content)
        if subject and subject in self.responses:
            return self.responses[subject]
        return EMPTY_EXTRACTION


# ── selection ────────────────────────────────────────────────────────────────


def build_provider() -> ExtractionProvider:
    """Construct the provider named by settings.

    `LLM_PROVIDER=auto` (the default) picks OpenRouter when a key is present
    and the offline stub otherwise, so a fresh clone with no `.env` still
    runs the full pipeline end to end.
    """
    from backend.config import PROJECT_ROOT, get_settings

    settings = get_settings()
    if settings.resolved_provider == "openrouter":
        assert settings.openrouter_api_key is not None
        return OpenRouterProvider(
            api_key=settings.openrouter_api_key,
            model=settings.model_name,
            base_url=settings.openrouter_base_url,
        )
    return StubProvider.from_fixtures(PROJECT_ROOT / "backend" / "fixtures")
