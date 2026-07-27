from __future__ import annotations

from pydantic import BaseModel


class RequestStats(BaseModel):
    count: int
    avg_latency_ms: float | None = None


class CardinalityStats(BaseModel):
    enforced_overwrites: int
    conflicts_deferred: int
    conflicts_promoted: int
    conflicts_discarded: int


class ValidatorStats(BaseModel):
    invocations: int
    confirmed: int
    refreshed: int
    timeouts: int
    failed: int


class ContradictionStats(BaseModel):
    resolved_total: int
    unresolved_total: int


class DecayStats(BaseModel):
    facts_with_misses: int
    total_retrieval_count: int
    total_miss_count: int


class TelemetryStatsResponse(BaseModel):
    requests: dict[str, RequestStats]
    cardinality: CardinalityStats
    validators: ValidatorStats
    contradictions: ContradictionStats
    decay: DecayStats
    provenance_rejected_total: int
    write_rollback_total: int


class UnresolvedContradictionEntry(BaseModel):
    id: str
    query_text: str
    conflicting_fact_ids: list[str]
    entity: str | None
    attribute: str | None
    detected_at: str


class DecayFlaggedCandidate(BaseModel):
    fact_id: str
    entity: str
    attribute: str
    miss_count: int
    retrieval_count: int


class TelemetryContradictionsResponse(BaseModel):
    unresolved_contradictions: list[UnresolvedContradictionEntry]
    decay_flagged_candidates: list[DecayFlaggedCandidate]


class ProvenanceStatusCount(BaseModel):
    provenance: str
    status: str
    count: int


class ProvenanceAnomaly(BaseModel):
    fact_id: str
    entity: str
    attribute: str
    reason: str


class TelemetryProvenanceAuditResponse(BaseModel):
    counts: list[ProvenanceStatusCount]
    anomalies: list[ProvenanceAnomaly]
