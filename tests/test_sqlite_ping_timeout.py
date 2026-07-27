"""Task 10: SqliteStore.ping() had no timeout guard, unlike every other
network-shaped call in the codebase (query_timeout_ms, validator_timeout_ms).
A wedged connection would otherwise hang /health forever instead of
reporting the component unavailable."""

import asyncio

from helpers import build_client

from memanchor.db.sqlite.store import SqliteStore


async def test_ping_returns_true_on_healthy_connection(tmp_path):
    store = SqliteStore(str(tmp_path / "memanchor.db"))
    await store.connect()
    try:
        assert await store.ping(timeout_ms=1000) is True
    finally:
        await store.close()


async def test_ping_times_out_instead_of_hanging(tmp_path, monkeypatch):
    store = SqliteStore(str(tmp_path / "memanchor.db"))
    await store.connect()

    async def wedged_execute(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(store.conn, "execute", wedged_execute)

    elapsed_start = asyncio.get_event_loop().time()
    result = await store.ping(timeout_ms=50)
    elapsed = asyncio.get_event_loop().time() - elapsed_start

    assert result is False
    assert elapsed < 1.0

    await store.close()


async def test_health_endpoint_reports_ok_when_stores_are_healthy(tmp_path):
    async with build_client(tmp_path) as (client, app):
        r = await client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["components"]["sqlite"] == "ok"
        assert body["components"]["chroma"] == "ok"


async def test_health_endpoint_reports_degraded_when_sqlite_ping_times_out(tmp_path):
    async with build_client(tmp_path, sqlite_ping_timeout_ms=50) as (client, app):
        async def wedged_execute(*args, **kwargs):
            await asyncio.sleep(10)

        app.state.sqlite_store.conn.execute = wedged_execute

        r = await client.get("/health")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "degraded"
        assert body["components"]["sqlite"] == "unavailable"
