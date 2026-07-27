"""Query/retrieval pipeline: Chroma nearest-neighbor -> SQLite
post-retrieval filter (tombstones, provenance_pending, decay-weighted
confidence) -> validator revalidation (confirms or refreshes
system-derived candidates with a registered validator) -> semantic
contradiction check among the remaining, unvalidated candidates, resolved
via query-time provenance priority with validator-confirmed ground truth
winning outright over any conflicting unvalidated candidate.

`status` is `ok` / `unresolved` / `degraded` / `error`: `degraded` appears
when a validator timed out or failed this round; affected results are
tagged `resolution: "pending"`.

Decay bookkeeping runs once per query, at the end, comparing
the post-tombstone-filter candidate set against whichever ids survive to
the final results -- covering decay-dropped, contradiction-losing, and
ground-truth-beaten candidates alike. Skipped entirely on `unresolved`
(a hard contradiction is already surfaced there) and excludes any old fact
id superseded by a validator refresh this round (a refresh isn't a miss).

`_query_memory_inner` is a stepdown table of contents over four stages,
each its own helper below in call order: retrieve -> decay+revalidate ->
resolve contradictions -> finalize the response.
"""

from __future__ import annotations

import asyncio
from typing import Any

from memanchor.config import Config
from memanchor.contracts.adapter import MemAnchorAdapter
from memanchor.domain.contradiction import UnresolvableContradiction, resolve_contradictions, values_conflict
from memanchor.domain.decay import apply_decay_weighting
from memanchor.domain.ids import new_id, now_iso
from memanchor.models import ErrorDetail, QueryRequest, QueryResponse, ResultItem
from memanchor.application.validator import revalidate_candidates
from memanchor.ports.stores import FactStore, VectorStore
from memanchor.telemetry.counters import TelemetryCounters


async def query_memory(
    fact_store: FactStore,
    vector_store: VectorStore,
    adapter: MemAnchorAdapter,
    req: QueryRequest,
    config: Config,
    telemetry: TelemetryCounters,
) -> QueryResponse:
    try:
        return await asyncio.wait_for(
            _query_memory_inner(fact_store, vector_store, adapter, req, config, telemetry),
            timeout=config.query_timeout_ms / 1000,
        )
    except asyncio.TimeoutError:
        return QueryResponse(
            status="error",
            results=[],
            contradictions_resolved=0,
            tombstones_filtered=0,
            error=ErrorDetail(code="QUERY_TIMEOUT", message="Query exceeded query_timeout_ms"),
        )


async def _query_memory_inner(
    fact_store: FactStore,
    vector_store: VectorStore,
    adapter: MemAnchorAdapter,
    req: QueryRequest,
    config: Config,
    telemetry: TelemetryCounters,
) -> QueryResponse:
    candidates, tombstones_filtered = await _retrieve_candidates(fact_store, vector_store, req)

    (
        revalidated,
        ground_truth,
        unvalidated,
        warnings,
        superseded_ids,
        original_candidate_ids,
    ) = await _apply_decay_and_revalidate(fact_store, vector_store, adapter, candidates, config, req, telemetry)

    try:
        surviving_ids, contradictions_resolved = _resolve_contradictions_and_ground_truth(
            ground_truth, unvalidated, config.contradiction_similarity_threshold
        )
    except UnresolvableContradiction as exc:
        return await _unresolved_response(fact_store, req, unvalidated, tombstones_filtered, exc, telemetry)

    return await _finalize_response(
        fact_store,
        revalidated,
        surviving_ids,
        contradictions_resolved,
        tombstones_filtered,
        warnings,
        original_candidate_ids,
        superseded_ids,
        telemetry,
    )


async def _retrieve_candidates(
    fact_store: FactStore,
    vector_store: VectorStore,
    req: QueryRequest,
) -> tuple[list[dict[str, Any]], int]:
    """Chroma nearest-neighbor search, batch-fetch the matching SQLite rows,
    and drop any that aren't active (tombstoned / provenance_pending)."""
    chroma_hits = await vector_store.query(req.query, top_k=req.top_k)
    facts_by_id = await fact_store.get_facts_by_ids([hit["id"] for hit in chroma_hits])

    tombstones_filtered = 0
    candidates: list[dict[str, Any]] = []

    for hit in chroma_hits:
        fact = facts_by_id.get(hit["id"])
        if fact is None:
            continue
        if fact["status"] != "active":
            tombstones_filtered += 1
            continue
        candidates.append({**fact, "embedding": hit["embedding"]})

    return candidates, tombstones_filtered


async def _apply_decay_and_revalidate(
    fact_store: FactStore,
    vector_store: VectorStore,
    adapter: MemAnchorAdapter,
    candidates: list[dict[str, Any]],
    config: Config,
    req: QueryRequest,
    telemetry: TelemetryCounters,
) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str], set[str], set[str]
]:
    """Decay-weighted confidence, subsuming a plain
    `confidence < min_confidence` check (effective_confidence <= confidence
    always). Then validator revalidation over the surviving candidates,
    split into ground-truth (confirmed/refreshed this round) and
    still-unvalidated candidates for the contradiction stage.

    Returns (revalidated, ground_truth, unvalidated, warnings,
    superseded_ids, original_candidate_ids) -- the last two feed the decay
    bookkeeping in `_finalize_response`.
    """
    decay_metrics_by_id = await fact_store.get_decay_metrics_by_ids([c["id"] for c in candidates])
    candidates = apply_decay_weighting(candidates, decay_metrics_by_id, config.decay_halflife, req.min_confidence)
    original_candidate_ids = {c["id"] for c in candidates}

    revalidated, ground_truth_ids, warnings, superseded_ids = await revalidate_candidates(
        fact_store,
        vector_store,
        adapter,
        candidates,
        config.validator_timeout_ms,
        config.max_validators_per_query,
        telemetry,
    )

    ground_truth = [c for c in revalidated if c["id"] in ground_truth_ids]
    unvalidated = [c for c in revalidated if c["id"] not in ground_truth_ids]

    return revalidated, ground_truth, unvalidated, warnings, superseded_ids, original_candidate_ids


def _resolve_contradictions_and_ground_truth(
    ground_truth: list[dict[str, Any]],
    unvalidated: list[dict[str, Any]],
    contradiction_similarity_threshold: float,
) -> tuple[set[str], int]:
    """Pairwise semantic contradiction resolution among the unvalidated
    candidates, then the ground-truth override: a validator-confirmed/
    refreshed fact beats any surviving unvalidated candidate sharing the
    same entity+attribute with a conflicting value. Raises
    UnresolvableContradiction unchanged, for the caller to surface as
    `status: "unresolved"` (CONFLICT_UNRESOLVABLE).
    """
    surviving_unvalidated, contradictions_resolved = resolve_contradictions(
        unvalidated, contradiction_similarity_threshold
    )

    final_unvalidated = []
    for u in surviving_unvalidated:
        beaten = any(
            g["entity"] == u["entity"]
            and g["attribute"] == u["attribute"]
            and values_conflict(g["value"], u["value"])
            for g in ground_truth
        )
        if beaten:
            contradictions_resolved += 1
        else:
            final_unvalidated.append(u)

    surviving_ids = {c["id"] for c in ground_truth} | {c["id"] for c in final_unvalidated}
    return surviving_ids, contradictions_resolved


async def _unresolved_response(
    fact_store: FactStore,
    req: QueryRequest,
    unvalidated: list[dict[str, Any]],
    tombstones_filtered: int,
    exc: UnresolvableContradiction,
    telemetry: TelemetryCounters,
) -> QueryResponse:
    """Persists the unresolved pair to contradiction_log (the durable audit
    log behind GET /telemetry/contradictions) and builds the
    CONFLICT_UNRESOLVABLE response."""
    telemetry.increment("contradictions_unresolved")
    by_id = {c["id"]: c for c in unvalidated}
    first_conflict = by_id.get(exc.conflicting_ids[0]) if exc.conflicting_ids else None
    await fact_store.insert_contradiction_log(
        new_id(),
        req.query,
        exc.conflicting_ids,
        first_conflict["entity"] if first_conflict else None,
        first_conflict["attribute"] if first_conflict else None,
        now_iso(),
    )
    return QueryResponse(
        status="unresolved",
        results=[],
        contradictions_resolved=0,
        tombstones_filtered=tombstones_filtered,
        error=ErrorDetail(
            code="CONFLICT_UNRESOLVABLE",
            message="Two conflicting facts with equal provenance rank and no registered validator",
            conflicting_ids=exc.conflicting_ids,
        ),
    )


async def _finalize_response(
    fact_store: FactStore,
    revalidated: list[dict[str, Any]],
    surviving_ids: set[str],
    contradictions_resolved: int,
    tombstones_filtered: int,
    warnings: list[str],
    original_candidate_ids: set[str],
    superseded_ids: set[str],
    telemetry: TelemetryCounters,
) -> QueryResponse:
    """ResultItem assembly, status/degraded determination (a validator
    timeout/failure this round degrades the response without emptying it),
    and decay bookkeeping: every candidate considered this round (minus any
    superseded by a validator refresh -- a refresh isn't a miss) either
    made it to the final results (retrieval_count) or didn't (miss_count)."""
    surviving = [c for c in revalidated if c["id"] in surviving_ids]

    results = [
        ResultItem(
            id=c["id"],
            entity=c["entity"],
            attribute=c["attribute"],
            value=c["value"],
            content=c["content"],
            confidence=c["confidence"],
            provenance=c["provenance"],
            source_channel=c["source_channel"],
            created_at=c["created_at"],
            last_validated=c.get("last_validated"),
            resolution=c.get("resolution"),
        )
        for c in surviving
    ]

    status = "ok"
    error = None
    if warnings:
        status = "degraded"
        first_code = warnings[0].split(":", 1)[0]
        error = ErrorDetail(code=first_code, message=warnings[0], warnings=warnings)

    if contradictions_resolved:
        telemetry.increment("contradictions_resolved", by=contradictions_resolved)

    timestamp = now_iso()
    bookkeeping = [
        fact_store.increment_retrieval_count(fact_id, timestamp)
        if fact_id in surviving_ids
        else fact_store.increment_miss_count(fact_id)
        for fact_id in original_candidate_ids - superseded_ids
    ]
    if bookkeeping:
        await asyncio.gather(*bookkeeping)

    return QueryResponse(
        status=status,
        results=results,
        contradictions_resolved=contradictions_resolved,
        tombstones_filtered=tombstones_filtered,
        error=error,
    )
