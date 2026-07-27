"""SqliteStore's five cohesive responsibilities, split into small repo
classes sharing the one aiosqlite connection SqliteStore owns. Each repo is
a plain data-access class with no knowledge of the others -- SqliteStore
(store.py) is the facade that still satisfies FactStore (ports/stores.py)
and forwards to these.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from memanchor.domain.ids import now_iso

STALE_PENDING_SECONDS = 30


class FactsRepo:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def insert_fact(self, fact: dict[str, Any]) -> None:
        await self._conn.execute(
            """
            INSERT INTO facts (
                id, vector_id, entity, attribute, value, content, provenance,
                source_channel, provenance_confidence, confidence, status,
                conflict_type, created_at, last_validated, superseded_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fact["id"],
                fact["vector_id"],
                fact["entity"],
                fact["attribute"],
                fact["value"],
                fact["content"],
                fact["provenance"],
                fact["source_channel"],
                fact["provenance_confidence"],
                fact["confidence"],
                fact["status"],
                fact.get("conflict_type"),
                fact["created_at"],
                fact.get("last_validated"),
                fact.get("superseded_by"),
            ),
        )
        await self._conn.commit()

    async def get_fact(self, fact_id: str) -> dict[str, Any] | None:
        cursor = await self._conn.execute("SELECT * FROM facts WHERE id=?", (fact_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_facts_by_ids(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        cursor = await self._conn.execute(
            f"SELECT * FROM facts WHERE id IN ({placeholders})", ids
        )
        rows = await cursor.fetchall()
        return {row["id"]: dict(row) for row in rows}

    async def set_fact_status(self, fact_id: str, status: str) -> None:
        await self._conn.execute(
            "UPDATE facts SET status=? WHERE id=?", (status, fact_id)
        )
        await self._conn.commit()

    async def tombstone_fact(self, fact_id: str, superseded_by: str) -> None:
        await self._conn.execute(
            "UPDATE facts SET status='tombstoned', superseded_by=? WHERE id=?",
            (superseded_by, fact_id),
        )
        await self._conn.commit()

    async def soft_delete_fact(self, fact_id: str) -> None:
        """Soft-delete via status tombstone without supersession chain (DELETE /memory/{id})."""
        await self._conn.execute(
            "UPDATE facts SET status='tombstoned' WHERE id=?", (fact_id,)
        )
        await self._conn.commit()

    async def discard_fact(self, fact_id: str) -> None:
        """Discard a pending cardinality-conflict record: tombstone it
        without superseding anything (it was never active)."""
        await self._conn.execute(
            "UPDATE facts SET status='tombstoned', conflict_type=NULL WHERE id=?",
            (fact_id,),
        )
        await self._conn.commit()

    async def finalize_promote_fact(self, fact_id: str) -> None:
        """Promote step 4: activate the pending record."""
        await self._conn.execute(
            "UPDATE facts SET status='active', conflict_type=NULL WHERE id=?",
            (fact_id,),
        )
        await self._conn.commit()

    async def set_fact_provenance(self, fact_id: str, provenance: str, confidence: float) -> None:
        await self._conn.execute(
            "UPDATE facts SET provenance=?, confidence=? WHERE id=?",
            (provenance, confidence, fact_id),
        )
        await self._conn.commit()

    async def set_last_validated(self, fact_id: str, timestamp: str) -> None:
        await self._conn.execute(
            "UPDATE facts SET last_validated=? WHERE id=?", (timestamp, fact_id)
        )
        await self._conn.commit()

    async def delete_fact(self, fact_id: str) -> None:
        await self._conn.execute("DELETE FROM facts WHERE id=?", (fact_id,))
        await self._conn.commit()

    async def list_stale_pending(
        self, older_than_seconds: int = STALE_PENDING_SECONDS
    ) -> list[dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        cursor = await self._conn.execute(
            "SELECT * FROM facts WHERE status='pending' AND created_at < ?", (cutoff,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_provenance_status_counts(self) -> list[dict[str, Any]]:
        cursor = await self._conn.execute(
            "SELECT provenance, status, COUNT(*) AS count FROM facts GROUP BY provenance, status"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def find_inferred_on_cardinality_one(self) -> list[dict[str, Any]]:
        cursor = await self._conn.execute(
            """
            SELECT f.id AS fact_id, f.entity, f.attribute
            FROM facts f
            JOIN entity_registry e ON e.entity = f.entity AND e.attribute = f.attribute
            WHERE e.cardinality = 'ONE' AND f.provenance = 'inferred' AND f.status = 'active'
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


class EntityRegistryRepo:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def get_entity_registry(self, entity: str, attribute: str) -> dict[str, Any] | None:
        cursor = await self._conn.execute(
            "SELECT * FROM entity_registry WHERE entity=? AND attribute=?",
            (entity, attribute),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def upsert_entity_registry(
        self, entity: str, attribute: str, cardinality: str, active_fact_id: str | None
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO entity_registry (entity, attribute, cardinality, active_fact_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (entity, attribute) DO UPDATE SET active_fact_id=excluded.active_fact_id
            """,
            (entity, attribute, cardinality, active_fact_id),
        )
        await self._conn.commit()


class ValidatorsRepo:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def register_validator(
        self, entity: str, attribute: str, validator_tool: str, adapter: str, observed_inputs: dict
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO validators (entity, attribute, validator_tool, adapter, observed_inputs, paired_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (entity, attribute) DO UPDATE SET
                validator_tool=excluded.validator_tool,
                adapter=excluded.adapter,
                observed_inputs=excluded.observed_inputs,
                paired_at=excluded.paired_at
            """,
            (entity, attribute, validator_tool, adapter, json.dumps(observed_inputs), now_iso()),
        )
        await self._conn.commit()

    async def get_validator(self, entity: str, attribute: str) -> dict[str, Any] | None:
        cursor = await self._conn.execute(
            "SELECT * FROM validators WHERE entity=? AND attribute=?", (entity, attribute)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        result = dict(row)
        result["observed_inputs"] = json.loads(result["observed_inputs"])
        return result

    async def get_validators_by_keys(
        self, keys: list[tuple[str, str]]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Batch form of get_validator(), mirroring get_facts_by_ids /
        get_decay_metrics_by_ids -- one query for a whole candidate set
        instead of one per (entity, attribute) pair."""
        if not keys:
            return {}
        placeholders = ",".join("(?,?)" for _ in keys)
        params = [value for pair in keys for value in pair]
        cursor = await self._conn.execute(
            f"SELECT * FROM validators WHERE (entity, attribute) IN ({placeholders})", params
        )
        rows = await cursor.fetchall()
        result: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            entry = dict(row)
            entry["observed_inputs"] = json.loads(entry["observed_inputs"])
            result[(entry["entity"], entry["attribute"])] = entry
        return result

    async def list_validators(self) -> list[dict[str, Any]]:
        cursor = await self._conn.execute("SELECT * FROM validators ORDER BY entity, attribute")
        rows = await cursor.fetchall()
        results = []
        for row in rows:
            entry = dict(row)
            entry["observed_inputs"] = json.loads(entry["observed_inputs"])
            results.append(entry)
        return results


class DecayMetricsRepo:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def get_decay_metrics_by_ids(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        cursor = await self._conn.execute(
            f"SELECT * FROM decay_metrics WHERE fact_id IN ({placeholders})", ids
        )
        rows = await cursor.fetchall()
        return {row["fact_id"]: dict(row) for row in rows}

    async def increment_retrieval_count(self, fact_id: str, timestamp: str) -> None:
        await self._conn.execute(
            """
            INSERT INTO decay_metrics (fact_id, retrieval_count, miss_count, last_retrieved)
            VALUES (?, 1, 0, ?)
            ON CONFLICT (fact_id) DO UPDATE SET
                retrieval_count = retrieval_count + 1,
                last_retrieved = excluded.last_retrieved
            """,
            (fact_id, timestamp),
        )
        await self._conn.commit()

    async def increment_miss_count(self, fact_id: str) -> None:
        await self._conn.execute(
            """
            INSERT INTO decay_metrics (fact_id, retrieval_count, miss_count, last_retrieved)
            VALUES (?, 0, 1, NULL)
            ON CONFLICT (fact_id) DO UPDATE SET
                miss_count = miss_count + 1
            """,
            (fact_id,),
        )
        await self._conn.commit()

    async def list_decay_flagged_facts(self, threshold: float) -> list[dict[str, Any]]:
        cursor = await self._conn.execute(
            """
            SELECT d.fact_id, f.entity, f.attribute, d.miss_count, d.retrieval_count
            FROM decay_metrics d
            JOIN facts f ON f.id = d.fact_id
            WHERE d.miss_count >= ? AND f.status = 'active'
            ORDER BY d.miss_count DESC
            """,
            (threshold,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_decay_summary(self) -> dict[str, Any]:
        cursor = await self._conn.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE miss_count > 0) AS facts_with_misses,
                COALESCE(SUM(retrieval_count), 0) AS total_retrieval_count,
                COALESCE(SUM(miss_count), 0) AS total_miss_count
            FROM decay_metrics
            """
        )
        row = await cursor.fetchone()
        return dict(row)


class ContradictionLogRepo:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def insert_contradiction_log(
        self,
        log_id: str,
        query_text: str,
        conflicting_fact_ids: list[str],
        entity: str | None,
        attribute: str | None,
        detected_at: str,
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO contradiction_log (id, query_text, conflicting_fact_ids, entity, attribute, detected_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (log_id, query_text, json.dumps(conflicting_fact_ids), entity, attribute, detected_at),
        )
        await self._conn.commit()

    async def list_contradiction_log(self, limit: int = 100) -> list[dict[str, Any]]:
        cursor = await self._conn.execute(
            "SELECT * FROM contradiction_log ORDER BY detected_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        results = []
        for row in rows:
            entry = dict(row)
            entry["conflicting_fact_ids"] = json.loads(entry["conflicting_fact_ids"])
            results.append(entry)
        return results
