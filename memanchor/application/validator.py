"""Validator re-invocation and staleness refresh: single-fact
tombstone+rewrite, distinct from the two-fact contradiction path in
domain/contradiction.py, which stays scoped to facts with no authoritative
tool source.

Runs as a first pass over query candidates, before contradiction detection:
system-derived candidates with a registered validator get re-invoked (up to
`max_validators_per_query` total per query) and are either confirmed,
refreshed, or flagged degraded (timeout/failed). Confirmed/refreshed
candidates are ground truth for this query and bypass the pairwise
contradiction step entirely (see query_pipeline.py).
"""

from __future__ import annotations

import asyncio
from typing import Any

from memanchor.application.write_pipeline import WriteIntent, transactional_write
from memanchor.contracts.adapter import MemAnchorAdapter
from memanchor.domain.contradiction import values_conflict
from memanchor.domain.ids import new_id, now_iso
from memanchor.ports.stores import FactStore, VectorStore
from memanchor.telemetry.counters import TelemetryCounters


class RefreshWriteFailed(Exception):
    """Raised by _refresh_stale_fact when its transactional_write doesn't
    succeed -- carries the underlying ErrorDetail.message through so the
    resulting VALIDATOR_FAILED warning keeps the real failure detail instead
    of a generic string."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


async def revalidate_candidates(
    fact_store: FactStore,
    vector_store: VectorStore,
    adapter: MemAnchorAdapter,
    candidates: list[dict[str, Any]],
    validator_timeout_ms: int,
    max_validators_per_query: int,
    telemetry: TelemetryCounters,
) -> tuple[list[dict[str, Any]], set[str], list[str], set[str]]:
    """Returns (candidates with any refreshed facts swapped in, ids of
    candidates confirmed/refreshed this round -- ground truth -- warning
    messages for any VALIDATOR_TIMEOUT/VALIDATOR_FAILED, and the ids of any
    old facts superseded by a refresh this round -- decay bookkeeping
    excludes these from miss_count, since a legitimate refresh isn't the
    same as "retrieved but not selected")."""
    warnings: list[str] = []
    ground_truth_ids: set[str] = set()
    superseded_ids: set[str] = set()
    invocations = 0
    result: list[dict[str, Any]] = []

    system_derived_keys = [
        (c["entity"], c["attribute"]) for c in candidates if c["provenance"] == "system-derived"
    ]
    validators_by_key = await fact_store.get_validators_by_keys(system_derived_keys)

    for candidate in candidates:
        if candidate["provenance"] != "system-derived" or invocations >= max_validators_per_query:
            result.append(candidate)
            continue

        validator_row = validators_by_key.get((candidate["entity"], candidate["attribute"]))
        if validator_row is None:
            result.append(candidate)
            continue

        callable_fn = adapter.get_validator(validator_row["validator_tool"])
        if callable_fn is None:
            # No in-process callable registered for this tool name via
            # adapter.register_validator_callable() -- adapter.get_validator()
            # has nothing to resolve to. Treated the same as an unreachable
            # validator.
            telemetry.increment("validator_failed")
            warnings.append(
                f"VALIDATOR_FAILED: no callable registered in-process for tool "
                f"'{validator_row['validator_tool']}' (entity={candidate['entity']}, "
                f"attribute={candidate['attribute']})"
            )
            candidate["resolution"] = "pending"
            result.append(candidate)
            continue

        invocations += 1
        telemetry.increment("validator_invocations")
        try:
            outcome = await asyncio.wait_for(
                callable_fn(validator_row["observed_inputs"]), timeout=validator_timeout_ms / 1000
            )
        except asyncio.TimeoutError:
            telemetry.increment("validator_timeouts")
            warnings.append(
                f"VALIDATOR_TIMEOUT: entity={candidate['entity']} attribute={candidate['attribute']} "
                f"tool={validator_row['validator_tool']}"
            )
            candidate["resolution"] = "pending"
            result.append(candidate)
            continue
        except Exception as exc:
            telemetry.increment("validator_failed")
            warnings.append(
                f"VALIDATOR_FAILED: entity={candidate['entity']} attribute={candidate['attribute']} "
                f"tool={validator_row['validator_tool']}: {exc}"
            )
            candidate["resolution"] = "pending"
            result.append(candidate)
            continue

        new_value = str(outcome.get("value", candidate["value"]))
        if not values_conflict(candidate["value"], new_value):
            # Confirmed: validator agrees with the stored value.
            telemetry.increment("validator_confirmed")
            timestamp = now_iso()
            await fact_store.set_last_validated(candidate["id"], timestamp)
            candidate["last_validated"] = timestamp
            ground_truth_ids.add(candidate["id"])
            result.append(candidate)
            continue

        try:
            refreshed = await _refresh_stale_fact(
                fact_store, vector_store, candidate, new_value, outcome.get("content"), telemetry
            )
        except RefreshWriteFailed as exc:
            telemetry.increment("validator_failed")
            warnings.append(
                f"VALIDATOR_FAILED: refresh write failed for entity={candidate['entity']} "
                f"attribute={candidate['attribute']}: {exc.message}"
            )
            candidate["resolution"] = "pending"
            result.append(candidate)
            continue

        telemetry.increment("validator_refreshed")
        ground_truth_ids.add(refreshed["id"])
        superseded_ids.add(candidate["id"])
        result.append(refreshed)

    return result, ground_truth_ids, warnings, superseded_ids


async def _refresh_stale_fact(
    fact_store: FactStore,
    vector_store: VectorStore,
    candidate: dict[str, Any],
    new_value: str,
    new_content: str | None,
    telemetry: TelemetryCounters,
) -> dict[str, Any]:
    """Single-fact refresh: tombstone the stale fact and write the
    validator's current value as a new active fact, reusing the same
    transactional write ordering a normal store uses.

    Raises RefreshWriteFailed if the write doesn't succeed."""
    registry_row = await fact_store.get_entity_registry(candidate["entity"], candidate["attribute"])
    cardinality = registry_row["cardinality"] if registry_row else "MANY"

    fact_id = new_id()
    content = new_content or f"{candidate['entity']} {candidate['attribute']} is now {new_value}"
    fact = {
        "id": fact_id,
        "vector_id": fact_id,
        "entity": candidate["entity"],
        "attribute": candidate["attribute"],
        "value": new_value,
        "content": content,
        "provenance": "system-derived",
        "source_channel": candidate["source_channel"],
        "provenance_confidence": candidate["provenance_confidence"],
        "confidence": 1.0,
        "created_at": now_iso(),
    }
    intent = WriteIntent(fact=fact, cardinality=cardinality, tombstone_id=candidate["id"])
    response = await transactional_write(fact_store, vector_store, intent, telemetry)
    if response.status != "ok":
        message = response.error.message if response.error else "unknown write failure"
        raise RefreshWriteFailed(message)
    return {**fact, "status": "active", "last_validated": now_iso()}
