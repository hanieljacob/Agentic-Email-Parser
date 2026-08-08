"""Email ingestion and the per-email pipeline trigger."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from backend import learning
from backend.ingest import ingest
from backend.pipeline import run_pipeline, run_pipeline_safely

router = APIRouter(prefix="/emails", tags=["emails"])


class IngestResponse(BaseModel):
    email_id: str
    is_new: bool


class AssignSupplierRequest(BaseModel):
    supplier_id: str
    retrigger: bool = True


@router.post("", response_model=IngestResponse)
async def ingest_email(
    request: Request,
    background_tasks: BackgroundTasks,
    no_pipeline: bool = False,
) -> IngestResponse:
    """Ingest a raw RFC 822 message.

    The pipeline runs in the background by default. Pass `no_pipeline=true`
    to ingest only, then drive the pipeline yourself — which is what the
    compose page does so it can show the result synchronously.
    """
    email_id, is_new = await ingest(await request.body())
    if is_new and not no_pipeline:
        background_tasks.add_task(run_pipeline_safely, email_id)
    return IngestResponse(email_id=email_id, is_new=is_new)


@router.post("/{email_id}/pipeline")
async def trigger_pipeline(email_id: str) -> dict[str, Any]:
    """Run extract → match synchronously and return the match summary."""
    try:
        return await run_pipeline(email_id)
    except LookupError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except Exception as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.post("/{email_id}/assign-supplier")
async def assign_supplier(
    email_id: str,
    body: AssignSupplierRequest,
) -> learning.AssignSupplierResult:
    try:
        return await learning.assign_supplier(
            email_id, body.supplier_id, body.retrigger
        )
    except LookupError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
