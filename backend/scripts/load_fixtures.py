"""Ingest the committed fixture emails through the real pipeline.

The only thing standing in for production here is the model: extraction goes
through StubProvider, everything downstream — matching, confidence scoring,
the auto-apply decision, writeback, the audit log — is the same code a live
email runs through.

The stub is constructed explicitly rather than read from LLM_PROVIDER, so the
demo cannot make a network call even on a machine that happens to have
OPENROUTER_API_KEY set.

    python -m backend.scripts.load_fixtures
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from backend.config import PROJECT_ROOT
from backend.db import close_pool, open_pool
from backend.ingest import ingest
from backend.llm import StubProvider
from backend.pipeline import run_pipeline

FIXTURES_DIR = PROJECT_ROOT / "backend" / "fixtures"


async def load_fixtures(fixtures_dir: Path = FIXTURES_DIR) -> dict[str, int]:
    """Ingest every fixture email and run the pipeline over it."""
    provider = StubProvider.from_fixtures(fixtures_dir)
    totals = {"emails": 0, "proposed": 0, "auto_applied": 0, "pending": 0}

    for eml_path in sorted(fixtures_dir.glob("*.eml")):
        email_id, _ = await ingest(eml_path.read_bytes())
        summary = await run_pipeline(email_id, provider=provider)

        totals["emails"] += 1
        totals["proposed"] += summary["proposed"]
        totals["auto_applied"] += summary["auto_applied"]
        totals["pending"] += summary["pending"]

        print(
            f"  ✓ {eml_path.name}"
            f"  proposed={summary['proposed']}"
            f"  auto-applied={summary['auto_applied']}"
            f"  pending={summary['pending']}"
        )

    return totals


async def main() -> None:
    await open_pool()
    try:
        totals = await load_fixtures()
        print(
            f"Loaded {totals['emails']} fixture emails: "
            f"{totals['proposed']} changes proposed, "
            f"{totals['auto_applied']} auto-applied, "
            f"{totals['pending']} awaiting review."
        )
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
