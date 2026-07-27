"""Contradiction/cardinality resolution: `cardinality_conflict_resolve`
(promote/discard) and `reclassify_provenance`.

`promote` must commit the deferred vector *before* touching the active
record -- getting that ordering backwards reopens the exact gap this
handler exists to close (see the step-by-step comments in `_promote` below
for why each step is ordered the way it is).
"""

from __future__ import annotations

from typing import Any

from memanchor.domain.provenance import base_confidence
from memanchor.models import ErrorDetail, ResolveRequest, ResolveResponse
from memanchor.ports.stores import FactStore, VectorStore
from memanchor.telemetry.counters import TelemetryCounters


async def resolve(
    fact_store: FactStore, vector_store: VectorStore, req: ResolveRequest, telemetry: TelemetryCounters
) -> ResolveResponse:
    if req.action == "cardinality_conflict_resolve":
        if req.resolution is None:
            return _invalid_state(req.fact_id, "resolution is required for cardinality_conflict_resolve")
        return await _resolve_cardinality_conflict(fact_store, vector_store, req, telemetry)

    if req.new_provenance is None:
        return _invalid_state(req.fact_id, "new_provenance is required for reclassify_provenance")
    return await _reclassify_provenance(fact_store, req)


async def _resolve_cardinality_conflict(
    fact_store: FactStore, vector_store: VectorStore, req: ResolveRequest, telemetry: TelemetryCounters
) -> ResolveResponse:
    pending = await fact_store.get_fact(req.fact_id)
    if pending is None:
        return _not_found(req.fact_id)
    if pending["status"] != "provenance_pending" or pending["conflict_type"] != "cardinality":
        return _invalid_state(
            req.fact_id,
            f"fact {req.fact_id} is not a pending cardinality conflict "
            f"(status={pending['status']!r}, conflict_type={pending['conflict_type']!r})",
        )

    if req.resolution == "discard":
        # Discard: SQLite-only, active record/vector untouched.
        await fact_store.discard_fact(pending["id"])
        telemetry.increment("cardinality_conflict_discarded")
        return ResolveResponse(status="ok", id=pending["id"])

    return await _promote(fact_store, vector_store, pending, telemetry)


async def _promote(
    fact_store: FactStore, vector_store: VectorStore, pending: dict[str, Any], telemetry: TelemetryCounters
) -> ResolveResponse:
    registry_row = await fact_store.get_entity_registry(pending["entity"], pending["attribute"])
    active = None
    if registry_row and registry_row["active_fact_id"]:
        active = await fact_store.get_fact(registry_row["active_fact_id"])

    # Step 2: brief mid-write marker. A crash here is caught by the startup
    # cleanup's 30s-stale sweep (cleanup_stale_pending); worst case is
    # losing the promote candidate -- the active record is only touched in
    # step 4, after the new vector is confirmed committed, so there is no
    # window where the entity is left without a vector behind its active fact.
    await fact_store.set_fact_status(pending["id"], "pending")

    # Step 3: commit the new vector before touching the active record.
    try:
        await vector_store.upsert(pending["id"], pending["content"])
    except Exception as exc:
        await fact_store.set_fact_status(pending["id"], "provenance_pending")
        return ResolveResponse(
            status="error", id=pending["id"], error=ErrorDetail(code="WRITE_ROLLBACK", message=str(exc))
        )

    # Step 4: only now invalidate the old active record. A failure here
    # would otherwise leave the pending fact stuck at the mid-write
    # 'pending' marker forever (only the startup sweep clears those, and
    # only after older_than_seconds) with a live, unreferenced vector
    # behind it -- so it gets the same revert-and-report treatment step 3
    # already has, cleaning up the vector we just committed.
    try:
        if active is not None:
            await fact_store.tombstone_fact(active["id"], superseded_by=pending["id"])
        await fact_store.upsert_entity_registry(
            pending["entity"], pending["attribute"], registry_row["cardinality"], pending["id"]
        )
        await fact_store.finalize_promote_fact(pending["id"])
    except Exception as exc:
        try:
            await vector_store.delete([pending["vector_id"]])
        except Exception:
            pass
        await fact_store.set_fact_status(pending["id"], "provenance_pending")
        return ResolveResponse(
            status="error", id=pending["id"], error=ErrorDetail(code="WRITE_ROLLBACK", message=str(exc))
        )

    # Step 5: best-effort cleanup of the now-superseded vector -- a harmless
    # orphan if it fails, since the tombstoned fact's id no longer matches
    # any active_fact_id and will never be retrieved regardless.
    if active is not None:
        try:
            await vector_store.delete([active["vector_id"]])
        except Exception:
            pass

    telemetry.increment("cardinality_conflict_promoted")
    return ResolveResponse(status="ok", id=pending["id"])


async def _reclassify_provenance(fact_store: FactStore, req: ResolveRequest) -> ResolveResponse:
    fact = await fact_store.get_fact(req.fact_id)
    if fact is None:
        return _not_found(req.fact_id)
    new_confidence = base_confidence(req.new_provenance, fact["provenance_confidence"])
    await fact_store.set_fact_provenance(req.fact_id, req.new_provenance, new_confidence)
    return ResolveResponse(status="ok", id=req.fact_id)


def _not_found(fact_id: str) -> ResolveResponse:
    return ResolveResponse(
        status="error", id=fact_id, error=ErrorDetail(code="FACT_NOT_FOUND", message=f"no fact with id {fact_id}")
    )


def _invalid_state(fact_id: str, message: str) -> ResolveResponse:
    return ResolveResponse(
        status="error", id=fact_id, error=ErrorDetail(code="INVALID_RESOLVE_STATE", message=message)
    )
