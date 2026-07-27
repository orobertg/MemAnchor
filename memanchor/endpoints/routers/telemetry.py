"""Telemetry read routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from memanchor.application.telemetry_service import (
    build_contradictions,
    build_provenance_audit,
    build_stats,
)
from memanchor.models import (
    TelemetryContradictionsResponse,
    TelemetryProvenanceAuditResponse,
    TelemetryStatsResponse,
)

router = APIRouter(tags=["telemetry"])


@router.get("/telemetry/stats", response_model=TelemetryStatsResponse)
async def telemetry_stats(request: Request):
    state = request.app.state
    return await build_stats(state.telemetry, state.sqlite_store)


@router.get("/telemetry/contradictions", response_model=TelemetryContradictionsResponse)
async def telemetry_contradictions(request: Request):
    state = request.app.state
    return await build_contradictions(state.sqlite_store, state.config.decay_halflife)


@router.get("/telemetry/provenance-audit", response_model=TelemetryProvenanceAuditResponse)
async def telemetry_provenance_audit(request: Request):
    return await build_provenance_audit(request.app.state.sqlite_store)
