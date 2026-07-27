"""Active observation ingest route."""

from __future__ import annotations

from fastapi import APIRouter, Request

from memanchor.application.ingest_service import ingest_tool_result
from memanchor.endpoints.errors import error_response
from memanchor.endpoints.timing import timed
from memanchor.models import IngestRequest, IngestResponse

router = APIRouter(tags=["observer"])


@router.post("/observer/ingest", response_model=IngestResponse)
async def observer_ingest(req: IngestRequest, request: Request):
    state = request.app.state
    response = await timed(
        state.telemetry, "observer_ingest", ingest_tool_result(state.adapter, state.ingest_dedup_cache, req)
    )
    if response.status == "error" and response.error:
        return error_response(response, response.error.code)
    return response
