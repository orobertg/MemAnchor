"""Memory lifecycle helpers used by the presentation layer.

Soft-delete and health checks stay out of routers so the HTTP layer never
touches raw connections or storage internals.
"""

from __future__ import annotations

from typing import Any

from memanchor.ports.stores import FactStore, VectorStore


class FactNotFoundError(Exception):
    def __init__(self, fact_id: str):
        self.fact_id = fact_id
        super().__init__(f"fact not found: {fact_id}")


async def soft_delete_fact(fact_store: FactStore, fact_id: str) -> str:
    fact = await fact_store.get_fact(fact_id)
    if fact is None:
        raise FactNotFoundError(fact_id)
    await fact_store.soft_delete_fact(fact_id)
    return fact_id


async def component_health(
    fact_store: FactStore, vector_store: VectorStore, sqlite_ping_timeout_ms: int = 2000
) -> dict[str, Any]:
    sqlite_ok = await fact_store.ping(timeout_ms=sqlite_ping_timeout_ms)
    chroma_ok = await vector_store.heartbeat()
    healthy = sqlite_ok and chroma_ok
    return {
        "healthy": healthy,
        "status": "ok" if healthy else "degraded",
        "components": {
            "sqlite": "ok" if sqlite_ok else "unavailable",
            "chroma": "ok" if chroma_ok else "unavailable",
        },
    }
