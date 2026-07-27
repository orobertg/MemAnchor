"""Memory write / query / resolve / soft-delete routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from memanchor.application.memory_service import FactNotFoundError, soft_delete_fact
from memanchor.application.query_pipeline import query_memory
from memanchor.application.resolve_pipeline import resolve
from memanchor.application.write_pipeline import store_fact
from memanchor.endpoints.errors import error_response
from memanchor.endpoints.timing import timed
from memanchor.models import QueryRequest, QueryResponse, ResolveRequest, ResolveResponse, StoreRequest, StoreResponse

router = APIRouter(tags=["memory"])


@router.post("/memory/store", response_model=StoreResponse)
async def memory_store(req: StoreRequest, request: Request):
    state = request.app.state
    response = await timed(
        state.telemetry, "memory_store", store_fact(state.sqlite_store, state.chroma_store, req, state.telemetry)
    )
    if response.status == "error" and response.error:
        return error_response(response, response.error.code)
    return response


@router.post("/memory/query", response_model=QueryResponse)
async def memory_query(req: QueryRequest, request: Request):
    state = request.app.state
    response = await timed(
        state.telemetry,
        "memory_query",
        query_memory(state.sqlite_store, state.chroma_store, state.adapter, req, state.config, state.telemetry),
    )
    if response.status == "error" and response.error:
        return error_response(response, response.error.code)
    return response


@router.post("/memory/resolve", response_model=ResolveResponse)
async def memory_resolve(req: ResolveRequest, request: Request):
    state = request.app.state
    response = await timed(
        state.telemetry, "memory_resolve", resolve(state.sqlite_store, state.chroma_store, req, state.telemetry)
    )
    if response.status == "error" and response.error:
        return error_response(response, response.error.code)
    return response


@router.delete("/memory/{fact_id}")
async def memory_delete(fact_id: str, request: Request):
    # Intentionally not instrumented via timed() -- this route has never
    # recorded latency (no "memory_delete" telemetry key exists today);
    # adding one would be a new capability, not a mechanical dedup.
    try:
        deleted_id = await soft_delete_fact(request.app.state.sqlite_store, fact_id)
    except FactNotFoundError:
        raise HTTPException(status_code=404, detail="fact not found") from None
    return {"status": "ok", "id": deleted_id}
