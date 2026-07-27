from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite

from memanchor.db.sqlite.repos import (
    ContradictionLogRepo,
    DecayMetricsRepo,
    EntityRegistryRepo,
    FactsRepo,
    ValidatorsRepo,
)
from memanchor.db.sqlite.schema import SCHEMA

STALE_PENDING_SECONDS = 30


class SqliteStore:
    """Facade satisfying the FactStore Protocol (ports/stores.py) -- owns
    the single connection and forwards to the five repo classes in
    repos.py, each covering one of facts / entity_registry / validators /
    decay_metrics / contradiction_log."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._facts: FactsRepo | None = None
        self._registry: EntityRegistryRepo | None = None
        self._validators: ValidatorsRepo | None = None
        self._decay: DecayMetricsRepo | None = None
        self._log: ContradictionLogRepo | None = None

    async def connect(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

        self._facts = FactsRepo(self._conn)
        self._registry = EntityRegistryRepo(self._conn)
        self._validators = ValidatorsRepo(self._conn)
        self._decay = DecayMetricsRepo(self._conn)
        self._log = ContradictionLogRepo(self._conn)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "SqliteStore.connect() must be called first"
        return self._conn

    async def ping(self, timeout_ms: int = 2000) -> bool:
        """Liveness probe for /health — never exposes the raw connection to
        callers. Bounded like every other network-shaped call in the
        codebase (query_timeout_ms, validator_timeout_ms), so a wedged
        connection reports unavailable instead of hanging /health forever."""
        try:
            await asyncio.wait_for(self.conn.execute("SELECT 1"), timeout=timeout_ms / 1000)
            return True
        except Exception:
            return False

    # --- facts ---

    async def insert_fact(self, fact: dict[str, Any]) -> None:
        return await self._facts.insert_fact(fact)

    async def get_fact(self, fact_id: str) -> dict[str, Any] | None:
        return await self._facts.get_fact(fact_id)

    async def get_facts_by_ids(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        return await self._facts.get_facts_by_ids(ids)

    async def set_fact_status(self, fact_id: str, status: str) -> None:
        return await self._facts.set_fact_status(fact_id, status)

    async def tombstone_fact(self, fact_id: str, superseded_by: str) -> None:
        return await self._facts.tombstone_fact(fact_id, superseded_by)

    async def soft_delete_fact(self, fact_id: str) -> None:
        return await self._facts.soft_delete_fact(fact_id)

    async def discard_fact(self, fact_id: str) -> None:
        return await self._facts.discard_fact(fact_id)

    async def finalize_promote_fact(self, fact_id: str) -> None:
        return await self._facts.finalize_promote_fact(fact_id)

    async def set_fact_provenance(self, fact_id: str, provenance: str, confidence: float) -> None:
        return await self._facts.set_fact_provenance(fact_id, provenance, confidence)

    async def set_last_validated(self, fact_id: str, timestamp: str) -> None:
        return await self._facts.set_last_validated(fact_id, timestamp)

    async def delete_fact(self, fact_id: str) -> None:
        return await self._facts.delete_fact(fact_id)

    async def list_stale_pending(
        self, older_than_seconds: int = STALE_PENDING_SECONDS
    ) -> list[dict[str, Any]]:
        return await self._facts.list_stale_pending(older_than_seconds)

    async def get_provenance_status_counts(self) -> list[dict[str, Any]]:
        return await self._facts.get_provenance_status_counts()

    async def find_inferred_on_cardinality_one(self) -> list[dict[str, Any]]:
        return await self._facts.find_inferred_on_cardinality_one()

    # --- entity registry ---

    async def get_entity_registry(self, entity: str, attribute: str) -> dict[str, Any] | None:
        return await self._registry.get_entity_registry(entity, attribute)

    async def upsert_entity_registry(
        self, entity: str, attribute: str, cardinality: str, active_fact_id: str | None
    ) -> None:
        return await self._registry.upsert_entity_registry(entity, attribute, cardinality, active_fact_id)

    # --- validators ---

    async def register_validator(
        self, entity: str, attribute: str, validator_tool: str, adapter: str, observed_inputs: dict
    ) -> None:
        return await self._validators.register_validator(
            entity, attribute, validator_tool, adapter, observed_inputs
        )

    async def get_validator(self, entity: str, attribute: str) -> dict[str, Any] | None:
        return await self._validators.get_validator(entity, attribute)

    async def get_validators_by_keys(
        self, keys: list[tuple[str, str]]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        return await self._validators.get_validators_by_keys(keys)

    async def list_validators(self) -> list[dict[str, Any]]:
        return await self._validators.list_validators()

    # --- decay metrics ---

    async def get_decay_metrics_by_ids(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        return await self._decay.get_decay_metrics_by_ids(ids)

    async def increment_retrieval_count(self, fact_id: str, timestamp: str) -> None:
        return await self._decay.increment_retrieval_count(fact_id, timestamp)

    async def increment_miss_count(self, fact_id: str) -> None:
        return await self._decay.increment_miss_count(fact_id)

    async def list_decay_flagged_facts(self, threshold: float) -> list[dict[str, Any]]:
        return await self._decay.list_decay_flagged_facts(threshold)

    async def get_decay_summary(self) -> dict[str, Any]:
        return await self._decay.get_decay_summary()

    # --- contradiction log ---

    async def insert_contradiction_log(
        self,
        log_id: str,
        query_text: str,
        conflicting_fact_ids: list[str],
        entity: str | None,
        attribute: str | None,
        detected_at: str,
    ) -> None:
        return await self._log.insert_contradiction_log(
            log_id, query_text, conflicting_fact_ids, entity, attribute, detected_at
        )

    async def list_contradiction_log(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._log.list_contradiction_log(limit)
