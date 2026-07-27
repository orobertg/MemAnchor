"""Provenance ranks and confidence defaults.

Write-time rank (cardinality ONE enforcement) and query-time rank (semantic
contradiction resolution with no validator involved) are intentionally
different orders: at write time a `user-stated` fact should outrank a
`system-derived` overwrite attempt, but at query time an unvalidated
`system-derived` candidate should outrank an unvalidated `inferred` one
while still losing to any validator-confirmed fact.
"""

WRITE_RANK = {"user-stated": 3, "system-derived": 2, "inferred": 1}
QUERY_RANK = {"system-derived": 3, "user-stated": 2, "inferred": 1}

DEFAULT_PROVENANCE_CONFIDENCE = {
    "system-derived": 1.0,
    "user-stated": 0.95,
    "inferred": 0.70,
}


def resolve_provenance_confidence(provenance: str, provenance_confidence: float | None) -> float:
    if provenance_confidence is not None:
        return provenance_confidence
    return DEFAULT_PROVENANCE_CONFIDENCE[provenance]


def base_confidence(provenance: str, provenance_confidence: float | None) -> float:
    """base_confidence at write time."""
    if provenance == "system-derived":
        return DEFAULT_PROVENANCE_CONFIDENCE["system-derived"]
    return resolve_provenance_confidence(provenance, provenance_confidence)
