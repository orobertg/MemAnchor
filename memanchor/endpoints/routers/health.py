"""Liveness / component health route."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from memanchor.application.memory_service import component_health

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request):
    state = request.app.state
    report = await component_health(
        state.sqlite_store, state.chroma_store, sqlite_ping_timeout_ms=state.config.sqlite_ping_timeout_ms
    )
    return JSONResponse(
        status_code=200 if report["healthy"] else 503,
        content={
            "status": report["status"],
            "components": report["components"],
        },
    )
