from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from memanchor.models.common import Cardinality, ErrorDetail, Provenance


class StoreRequest(BaseModel):
    entity: str
    attribute: str
    value: str
    content: str
    provenance: Provenance
    source_channel: str
    provenance_confidence: float | None = None
    cardinality: Cardinality = "MANY"
    force_provenance: bool = False


class StoreResponse(BaseModel):
    status: Literal["ok", "pending", "error"]
    id: str | None
    error: ErrorDetail | None = None


class QueryRequest(BaseModel):
    query: str
    top_k: int = 5
    min_confidence: float = 0.0


class ResultItem(BaseModel):
    id: str
    entity: str
    attribute: str
    value: str
    content: str
    confidence: float
    provenance: Provenance
    source_channel: str
    created_at: str
    last_validated: str | None = None
    resolution: str | None = None


class QueryResponse(BaseModel):
    status: Literal["ok", "unresolved", "degraded", "error"]
    results: list[ResultItem]
    contradictions_resolved: int
    tombstones_filtered: int
    error: ErrorDetail | None = None


class ResolveRequest(BaseModel):
    action: Literal["cardinality_conflict_resolve", "reclassify_provenance"]
    fact_id: str
    resolution: Literal["promote", "discard"] | None = None
    new_provenance: Provenance | None = None
    reason: str | None = None


class ResolveResponse(BaseModel):
    status: Literal["ok", "error"]
    id: str | None
    error: ErrorDetail | None = None
