"""API / boundary DTOs (Pydantic). Split by resource; re-exported here for
stable ``from memanchor.models import …`` imports.
"""

from memanchor.models.common import Cardinality, ErrorDetail, Provenance
from memanchor.models.memory import (
    QueryRequest,
    QueryResponse,
    ResolveRequest,
    ResolveResponse,
    ResultItem,
    StoreRequest,
    StoreResponse,
)
from memanchor.models.observer import IngestRequest, IngestResponse
from memanchor.models.registry import (
    RegistryListResponse,
    RegistryRegisterRequest,
    RegistryRegisterResponse,
    ValidatorEntry,
)
from memanchor.models.telemetry import (
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

__all__ = [
    "Cardinality",
    "CardinalityStats",
    "ContradictionStats",
    "DecayFlaggedCandidate",
    "DecayStats",
    "ErrorDetail",
    "IngestRequest",
    "IngestResponse",
    "Provenance",
    "ProvenanceAnomaly",
    "ProvenanceStatusCount",
    "QueryRequest",
    "QueryResponse",
    "RegistryListResponse",
    "RegistryRegisterRequest",
    "RegistryRegisterResponse",
    "RequestStats",
    "ResolveRequest",
    "ResolveResponse",
    "ResultItem",
    "StoreRequest",
    "StoreResponse",
    "TelemetryContradictionsResponse",
    "TelemetryProvenanceAuditResponse",
    "TelemetryStatsResponse",
    "UnresolvedContradictionEntry",
    "ValidatorEntry",
    "ValidatorStats",
]
