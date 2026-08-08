"""Pydantic models for LLM extraction output.

These replace the zod schema the TypeScript extractor used. The same models
serve two purposes:

  1. they are converted to a JSON schema and sent to the model as
     `response_format`, so the provider constrains generation, and
  2. they validate whatever actually comes back, before any of it is
     allowed near the database.

Validation failure is never fatal to the pipeline: `extract()` records the
error on the `extraction_runs` row and marks the email `failed`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

UpdatableField = Literal["delivery_date", "quantity"]


class LineUpdate(BaseModel):
    """A single field change to one purchase order line."""

    sku_or_code: str
    field: UpdatableField
    new_value: str
    evidence: str
    confidence: float = Field(ge=0.0, le=1.0)


class POUpdate(BaseModel):
    """All changes the email makes to one purchase order."""

    po_ref: str
    source: str = "body"
    evidence: str
    confidence: float = Field(ge=0.0, le=1.0)
    line_updates: list[LineUpdate] = Field(default_factory=list)


class ExtractionOutput(BaseModel):
    """Top-level shape the model is asked to return."""

    po_updates: list[POUpdate] = Field(default_factory=list)
    unmatched_mentions: list[str] = Field(default_factory=list)


# ── JSON schema for the provider ─────────────────────────────────────────────


def _tighten(node: dict[str, Any]) -> None:
    """Recursively mark every object closed and all of its keys required.

    OpenAI-compatible structured output rejects schemas that allow extra
    properties or leave any property optional, but Pydantic omits fields
    that carry defaults from `required`. Walk the schema and add them back.
    """
    if node.get("type") == "object" and "properties" in node:
        node["additionalProperties"] = False
        node["required"] = list(node["properties"].keys())

    for key in ("properties", "$defs"):
        for child in node.get(key, {}).values():
            if isinstance(child, dict):
                _tighten(child)

    items = node.get("items")
    if isinstance(items, dict):
        _tighten(items)


def extraction_json_schema() -> dict[str, Any]:
    """The JSON schema sent to the LLM, derived from `ExtractionOutput`."""
    schema = ExtractionOutput.model_json_schema()
    _tighten(schema)
    return schema
