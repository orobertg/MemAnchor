"""Value conflict rules and semantic contradiction resolution -- the
no-validator path: by the time candidates reach `resolve_contradictions`,
any validator-confirmed/refreshed ones have already been filtered out as
ground truth upstream (see application/validator.py), so every remaining
contradiction here falls through to provenance priority."""

from __future__ import annotations

import json
from itertools import combinations
from typing import Any

from memanchor.domain.provenance import QUERY_RANK


def _normalize(value: str) -> tuple[str, Any]:
    s = value.strip()
    lowered = s.lower()
    if lowered in ("true", "false"):
        return ("bool", lowered == "true")
    try:
        return ("number", float(s))
    except ValueError:
        pass
    try:
        parsed = json.loads(s)
        if isinstance(parsed, (dict, list)):
            return ("json", json.dumps(parsed, sort_keys=True))
    except (json.JSONDecodeError, TypeError):
        pass
    return ("string", lowered)


def values_conflict(a: str, b: str) -> bool:
    """Two values conflict when their normalized forms differ."""
    kind_a, norm_a = _normalize(a)
    kind_b, norm_b = _normalize(b)
    if kind_a != kind_b:
        return a.strip().lower() != b.strip().lower()
    if kind_a == "number":
        return abs(norm_a - norm_b) > 1e-6
    return norm_a != norm_b


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class UnresolvableContradiction(Exception):
    def __init__(self, conflicting_ids: list[str]):
        self.conflicting_ids = conflicting_ids


def resolve_contradictions(
    candidates: list[dict[str, Any]], threshold: float
) -> tuple[list[dict[str, Any]], int]:
    """Pairwise semantic contradiction check across query candidates that
    reached here unvalidated (see the module docstring). Resolves via
    query-time provenance priority; a tie in provenance rank has no safe
    winner and raises UnresolvableContradiction (CONFLICT_UNRESOLVABLE).

    Returns (surviving_candidates, contradictions_resolved_count).
    """
    removed_ids: set[str] = set()
    resolved_count = 0

    for a, b in combinations(candidates, 2):
        if a["id"] in removed_ids or b["id"] in removed_ids:
            continue
        similarity = cosine_similarity(a["embedding"], b["embedding"])
        if similarity <= threshold:
            continue
        if not values_conflict(a["value"], b["value"]):
            continue

        rank_a = QUERY_RANK[a["provenance"]]
        rank_b = QUERY_RANK[b["provenance"]]
        if rank_a == rank_b:
            raise UnresolvableContradiction(conflicting_ids=[a["id"], b["id"]])

        loser = a if rank_a < rank_b else b
        removed_ids.add(loser["id"])
        resolved_count += 1

    surviving = [c for c in candidates if c["id"] not in removed_ids]
    return surviving, resolved_count
