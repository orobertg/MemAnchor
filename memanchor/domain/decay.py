"""Decay weighting: a passive contradiction signal distinct from the hard
cardinality-enforcement and semantic-contradiction paths -- a fact retrieved
as a query candidate but never selected for the final result accumulates a
miss_count, and its effective_confidence decays exponentially with it.
"""

from __future__ import annotations

import math
from typing import Any


def effective_confidence(base_confidence: float, miss_count: int, decay_halflife: float) -> float:
    return base_confidence * math.exp(-miss_count / decay_halflife)


def apply_decay_weighting(
    candidates: list[dict[str, Any]],
    decay_metrics_by_id: dict[str, dict[str, Any]],
    decay_halflife: float,
    min_confidence: float,
) -> list[dict[str, Any]]:
    """Recomputes each candidate's `confidence` field as effective_confidence
    (missing decay_metrics rows default to miss_count=0, i.e. no decay yet)
    and drops any candidate whose effective_confidence falls below
    `min_confidence`. Strictly subsumes a plain `confidence < min_confidence`
    check, since effective_confidence <= confidence always."""
    surviving = []
    for candidate in candidates:
        miss_count = decay_metrics_by_id.get(candidate["id"], {}).get("miss_count", 0)
        eff = effective_confidence(candidate["confidence"], miss_count, decay_halflife)
        if eff < min_confidence:
            continue
        surviving.append({**candidate, "confidence": eff})
    return surviving
