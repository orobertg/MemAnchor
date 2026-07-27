"""Telemetry read models.

Assembles response DTOs from the in-memory counters port and fact store
queries — presentation layer only maps HTTP.
"""

from __future__ import annotations

from memanchor.models import (
    CardinalityStats,
    ContradictionStats,
    DecayFlaggedCandidate,
    DecayStats,
    ProvenanceAnomaly,
    ProvenanceStatusCount,
    RequestStats,
    TelemetryContradictionsResponse,
    TelemetryProvenanceAuditResponse,
    TelemetryStatsResponse,
    UnresolvedContradictionEntry,
    ValidatorStats,
)
from memanchor.ports.stores import FactStore
from memanchor.telemetry.counters import TelemetryCounters

_LATENCY_ENDPOINTS = ("memory_store", "memory_query", "memory_resolve", "observer_ingest")


async def build_stats(
    telemetry: TelemetryCounters, fact_store: FactStore
) -> TelemetryStatsResponse:
    decay_summary = await fact_store.get_decay_summary()
    requests = {
        endpoint: RequestStats(
            count=telemetry.get_request_count(endpoint),
            avg_latency_ms=telemetry.get_avg_latency_ms(endpoint),
        )
        for endpoint in _LATENCY_ENDPOINTS
    }
    return TelemetryStatsResponse(
        requests=requests,
        cardinality=CardinalityStats(
            enforced_overwrites=telemetry.get_event_count("cardinality_enforced_overwrites"),
            conflicts_deferred=telemetry.get_event_count("cardinality_conflicts_deferred"),
            conflicts_promoted=telemetry.get_event_count("cardinality_conflict_promoted"),
            conflicts_discarded=telemetry.get_event_count("cardinality_conflict_discarded"),
        ),
        validators=ValidatorStats(
            invocations=telemetry.get_event_count("validator_invocations"),
            confirmed=telemetry.get_event_count("validator_confirmed"),
            refreshed=telemetry.get_event_count("validator_refreshed"),
            timeouts=telemetry.get_event_count("validator_timeouts"),
            failed=telemetry.get_event_count("validator_failed"),
        ),
        contradictions=ContradictionStats(
            resolved_total=telemetry.get_event_count("contradictions_resolved"),
            unresolved_total=telemetry.get_event_count("contradictions_unresolved"),
        ),
        decay=DecayStats(
            facts_with_misses=decay_summary["facts_with_misses"],
            total_retrieval_count=decay_summary["total_retrieval_count"],
            total_miss_count=decay_summary["total_miss_count"],
        ),
        provenance_rejected_total=telemetry.get_event_count("provenance_rejected"),
        write_rollback_total=telemetry.get_event_count("write_rollback"),
    )


async def build_contradictions(
    fact_store: FactStore, decay_halflife: float
) -> TelemetryContradictionsResponse:
    log_rows = await fact_store.list_contradiction_log()
    flagged_rows = await fact_store.list_decay_flagged_facts(decay_halflife)
    return TelemetryContradictionsResponse(
        unresolved_contradictions=[
            UnresolvedContradictionEntry(
                id=row["id"],
                query_text=row["query_text"],
                conflicting_fact_ids=row["conflicting_fact_ids"],
                entity=row["entity"],
                attribute=row["attribute"],
                detected_at=row["detected_at"],
            )
            for row in log_rows
        ],
        decay_flagged_candidates=[
            DecayFlaggedCandidate(
                fact_id=row["fact_id"],
                entity=row["entity"],
                attribute=row["attribute"],
                miss_count=row["miss_count"],
                retrieval_count=row["retrieval_count"],
            )
            for row in flagged_rows
        ],
    )


async def build_provenance_audit(fact_store: FactStore) -> TelemetryProvenanceAuditResponse:
    count_rows = await fact_store.get_provenance_status_counts()
    anomaly_rows = await fact_store.find_inferred_on_cardinality_one()
    return TelemetryProvenanceAuditResponse(
        counts=[
            ProvenanceStatusCount(provenance=row["provenance"], status=row["status"], count=row["count"])
            for row in count_rows
        ],
        anomalies=[
            ProvenanceAnomaly(
                fact_id=row["fact_id"],
                entity=row["entity"],
                attribute=row["attribute"],
                reason="inferred provenance on cardinality-ONE attribute",
            )
            for row in anomaly_rows
        ],
    )
