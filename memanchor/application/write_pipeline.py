"""Write pipeline: provenance guardrail -> cardinality enforcement ->
transactional write with rollback. No validator pairing step here --
that's the adapter layer's job (see adapters/generic/adapter.py's
on_tool_call, which calls register_validator after a successful write).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from memanchor.domain.ids import new_id, now_iso
from memanchor.domain.provenance import WRITE_RANK, base_confidence, resolve_provenance_confidence
from memanchor.models import ErrorDetail, StoreRequest, StoreResponse
from memanchor.ports.stores import FactStore, VectorStore
from memanchor.telemetry.counters import TelemetryCounters


@dataclass
class WriteIntent:
    """Bundles transactional_write's three fact-shaped params (the rest --
    fact_store/vector_store/telemetry -- are dependencies, not intent data)
    so the signature reads as one thing to write, not four loose
    positionals."""

    fact: dict[str, Any]
    cardinality: str
    tombstone_id: str | None


async def store_fact(
    fact_store: FactStore, vector_store: VectorStore, req: StoreRequest, telemetry: TelemetryCounters
) -> StoreResponse:
    # Provenance guardrail: an inferred write on a cardinality-ONE attribute
    # is rejected outright unless force_provenance is set.
    if req.provenance == "inferred" and req.cardinality == "ONE" and not req.force_provenance:
        telemetry.increment("provenance_rejected")
        return StoreResponse(
            status="error",
            id=None,
            error=ErrorDetail(
                code="PROVENANCE_REJECTED",
                message=(
                    "inferred provenance rejected for cardinality ONE attribute "
                    "without force_provenance"
                ),
            ),
        )

    provenance_confidence = resolve_provenance_confidence(req.provenance, req.provenance_confidence)
    confidence = base_confidence(req.provenance, req.provenance_confidence)

    registry_row = await fact_store.get_entity_registry(req.entity, req.attribute)
    cardinality = registry_row["cardinality"] if registry_row else req.cardinality

    fact_id = new_id()
    fact: dict[str, Any] = {
        "id": fact_id,
        "vector_id": fact_id,
        "entity": req.entity,
        "attribute": req.attribute,
        "value": req.value,
        "content": req.content,
        "provenance": req.provenance,
        "source_channel": req.source_channel,
        "provenance_confidence": provenance_confidence,
        "confidence": confidence,
        "created_at": now_iso(),
    }

    if cardinality == "ONE" and registry_row and registry_row["active_fact_id"]:
        active = await fact_store.get_fact(registry_row["active_fact_id"])
        if active is not None:
            incoming_rank = WRITE_RANK[req.provenance]
            active_rank = WRITE_RANK[active["provenance"]]
            if incoming_rank < active_rank:
                return await _defer_cardinality_conflict(fact_store, fact, req, active, telemetry)
            telemetry.increment("cardinality_enforced_overwrites")
            intent = WriteIntent(fact=fact, cardinality=cardinality, tombstone_id=active["id"])
            return await transactional_write(fact_store, vector_store, intent, telemetry)

    intent = WriteIntent(fact=fact, cardinality=cardinality, tombstone_id=None)
    return await transactional_write(fact_store, vector_store, intent, telemetry)


async def _defer_cardinality_conflict(
    fact_store: FactStore,
    fact: dict[str, Any],
    req: StoreRequest,
    active: dict[str, Any],
    telemetry: TelemetryCounters,
) -> StoreResponse:
    """Cardinality conflict, deferred: SQLite-only insert as
    provenance_pending; no ChromaDB vector until promoted via
    /memory/resolve."""
    fact["status"] = "provenance_pending"
    fact["conflict_type"] = "cardinality"
    await fact_store.insert_fact(fact)
    telemetry.increment("cardinality_conflicts_deferred")
    return StoreResponse(
        status="pending",
        id=fact["id"],
        error=ErrorDetail(
            code="CARDINALITY_CONFLICT_PENDING",
            message=(
                f"Incoming {req.provenance} write ranks below active "
                f"{active['provenance']} fact for {req.entity}.{req.attribute}; "
                "held pending /memory/resolve (action: cardinality_conflict_resolve)"
            ),
        ),
    )


async def transactional_write(
    fact_store: FactStore,
    vector_store: VectorStore,
    intent: WriteIntent,
    telemetry: TelemetryCounters,
) -> StoreResponse:
    """Transactional write, ordered: SQLite pending -> Chroma upsert ->
    SQLite active + entity_registry update, with rollback on failure at
    either step. Also reused by application/validator.py for its
    single-fact staleness refresh, which is the same write shape with a
    different caller -- `write_rollback` telemetry is counted here so both
    callers get it for free."""
    fact = intent.fact
    cardinality = intent.cardinality
    tombstone_id = intent.tombstone_id
    fact["status"] = "pending"
    fact.setdefault("conflict_type", None)
    await fact_store.insert_fact(fact)

    try:
        await vector_store.upsert(fact["id"], fact["content"])
    except Exception as exc:
        await fact_store.delete_fact(fact["id"])
        telemetry.increment("write_rollback")
        return StoreResponse(
            status="error", id=None, error=ErrorDetail(code="WRITE_ROLLBACK", message=str(exc))
        )

    try:
        if tombstone_id is not None:
            await fact_store.tombstone_fact(tombstone_id, fact["id"])
        await fact_store.set_fact_status(fact["id"], "active")
        await fact_store.upsert_entity_registry(
            fact["entity"], fact["attribute"], cardinality, fact["id"]
        )
    except Exception as exc:
        await vector_store.delete([fact["id"]])
        await fact_store.delete_fact(fact["id"])
        telemetry.increment("write_rollback")
        return StoreResponse(
            status="error", id=None, error=ErrorDetail(code="WRITE_ROLLBACK", message=str(exc))
        )

    return StoreResponse(status="ok", id=fact["id"])


async def cleanup_stale_pending(
    fact_store: FactStore, vector_store: VectorStore, older_than_seconds: int = 30
) -> int:
    """Startup crash recovery: sweep status='pending' rows older than
    `older_than_seconds` (default 30s), deleting from Chroma then SQLite.
    Also covers a crash mid-promote, since promote reuses the same
    'pending' marker."""
    stale = await fact_store.list_stale_pending(older_than_seconds)
    await vector_store.delete([row["vector_id"] for row in stale])
    for row in stale:
        await fact_store.delete_fact(row["id"])
    return len(stale)


async def periodic_cleanup_sweep(
    fact_store: FactStore,
    vector_store: VectorStore,
    interval_seconds: float = 30,
    older_than_seconds: int = 30,
) -> None:
    """Long-running loop re-running cleanup_stale_pending every
    `interval_seconds`. Unlike the one-shot startup sweep, this recovers a
    fact that goes stale mid-runtime (e.g. a crash between transactional_write's
    steps) without requiring a process restart. Intended to run as a
    cancellable background asyncio.Task for the app's lifetime -- cancellation
    propagates as CancelledError, not swallowed here."""
    while True:
        await asyncio.sleep(interval_seconds)
        await cleanup_stale_pending(fact_store, vector_store, older_than_seconds)
