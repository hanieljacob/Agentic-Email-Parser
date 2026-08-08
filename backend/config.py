"""Typed application settings, read once from the environment.

Every knob the pipeline reads lives here so that `.env.example` can be
checked against a single file. Defaults are chosen so that a fresh clone
runs the whole pipeline offline with no API key set.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ProviderName = Literal["auto", "openrouter", "ollama", "stub"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── database ────────────────────────────────────────────────────────────
    database_url: str = "postgresql://localhost/email_parser"

    # ── LLM provider ────────────────────────────────────────────────────────
    # "auto" uses OpenRouter when OPENROUTER_API_KEY is set and the offline
    # stub otherwise, so the demo path never depends on a key being present.
    llm_provider: ProviderName = "auto"
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # A zero-cost model, so a clone with any OpenRouter key can run live
    # extraction without credit. Swap for a frontier model in production.
    model_name: str = "nvidia/nemotron-3-super-120b-a12b:free"

    # Providers to try, in order, when the primary one fails — a dead key, a
    # rate limit, an unreachable host. Comma-separated, e.g. "ollama,stub".
    # Empty (the default) means no fallback: a failed call fails the email.
    llm_fallback: str = ""

    # Ollama speaks the OpenAI API, so it needs no client of its own.
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "llama3.1"
    # Turn off if the local model rejects json_schema response_format; the
    # schema is in the system prompt either way and output is still validated.
    ollama_structured_output: bool = True

    # ── pipeline ────────────────────────────────────────────────────────────
    # Changes with combined_confidence >= this threshold are auto-applied
    # without human review.
    #
    # The comparison is `>=`, so 0 does NOT disable auto-apply — it applies
    # everything and empties the review queue. To send every change to a
    # human instead, set a value above 1.0.
    auto_apply_threshold: float = 0.95
    attachments_dir: Path = Path("./attachments")

    # ── retry worker ────────────────────────────────────────────────────────
    worker_enabled: bool = True
    worker_interval_seconds: int = 60
    worker_grace_seconds: int = 120
    worker_max_retries: int = 3

    # ── http ────────────────────────────────────────────────────────────────
    api_port: int = 8000
    cors_origins: list[str] = ["http://localhost:3000"]

    @property
    def resolved_provider(self) -> Literal["openrouter", "ollama", "stub"]:
        """Which provider `llm_provider="auto"` actually resolves to."""
        if self.llm_provider != "auto":
            return self.llm_provider
        return "openrouter" if self.openrouter_api_key else "stub"

    @property
    def fallback_providers(self) -> list[str]:
        """LLM_FALLBACK parsed into an ordered list of provider names."""
        return [name.strip() for name in self.llm_fallback.split(",") if name.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
