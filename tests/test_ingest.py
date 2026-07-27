"""POST /observer/ingest -- fresh writes, no-match no-op, idempotency dedup
within the 60s window, and the dedup cache's TTL logic in isolation."""

from helpers import build_client
from memanchor.observer.ingest import IngestDedupCache, compute_dedup_key


async def test_ingest_writes_a_fact(client):
    r = await client.post(
        "/observer/ingest",
        json={
            "adapter": "test",
            "tool_name": "check_gateway_port",
            "inputs": {"service": "gateway"},
            "outputs": {"entity": "gateway", "attribute": "port", "value": "8890"},
            "source_channel": "observer_ingest",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["id"] is not None
    assert body["deduplicated"] is False

    qr = await client.post(
        "/memory/query", json={"query": "what port does the gateway run on", "top_k": 5}
    )
    results = qr.json()["results"]
    assert len(results) == 1
    assert results[0]["id"] == body["id"]
    assert results[0]["value"] == "8890"


async def test_ingest_no_match_is_a_noop(client):
    r = await client.post(
        "/observer/ingest",
        json={
            "adapter": "test",
            "tool_name": "check_gateway_port",
            "inputs": {},
            "outputs": {"entity": "gateway", "attribute": "port"},  # no "value"
            "source_channel": "observer_ingest",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] is None
    assert body["deduplicated"] is False

    qr = await client.post(
        "/memory/query", json={"query": "what port does the gateway run on", "top_k": 5}
    )
    assert qr.json()["results"] == []


async def test_ingest_write_failure_reports_error_not_ok(tmp_path):
    # A real backend write failure must surface as status="error", not the
    # same "ok"/id=None shape as a genuine no-match no-op.
    async with build_client(tmp_path) as (client, app):
        async def failing_upsert(*args, **kwargs):
            raise RuntimeError("simulated chroma failure")

        app.state.chroma_store.upsert = failing_upsert

        r = await client.post(
            "/observer/ingest",
            json={
                "adapter": "test",
                "tool_name": "check_gateway_port",
                "inputs": {"service": "gateway"},
                "outputs": {"entity": "gateway", "attribute": "port", "value": "8890"},
                "source_channel": "observer_ingest",
            },
        )
        assert r.status_code == 500
        body = r.json()
        assert body["status"] == "error"
        assert body["id"] is None
        assert body["error"]["code"] == "WRITE_ROLLBACK"


async def test_ingest_duplicate_within_window_is_deduplicated(client):
    payload = {
        "adapter": "test",
        "tool_name": "check_gateway_port",
        "inputs": {"service": "gateway"},
        "outputs": {"entity": "gateway", "attribute": "port", "value": "8890"},
        "source_channel": "observer_ingest",
    }
    r1 = await client.post("/observer/ingest", json=payload)
    body1 = r1.json()
    assert body1["deduplicated"] is False

    r2 = await client.post("/observer/ingest", json=payload)
    body2 = r2.json()
    assert body2["deduplicated"] is True
    assert body2["id"] == body1["id"]

    qr = await client.post(
        "/memory/query", json={"query": "what port does the gateway run on", "top_k": 5}
    )
    assert len(qr.json()["results"]) == 1


async def test_ingest_different_payload_is_not_deduplicated(client):
    r1 = await client.post(
        "/observer/ingest",
        json={
            "adapter": "test",
            "tool_name": "check_gateway_port",
            "inputs": {"service": "gateway"},
            "outputs": {"entity": "gateway", "attribute": "port", "value": "8890"},
            "source_channel": "observer_ingest",
        },
    )
    r2 = await client.post(
        "/observer/ingest",
        json={
            "adapter": "test",
            "tool_name": "check_gateway_port",
            "inputs": {"service": "gateway"},
            "outputs": {"entity": "gateway", "attribute": "port", "value": "9999"},
            "source_channel": "observer_ingest",
        },
    )
    assert r2.json()["deduplicated"] is False
    assert r2.json()["id"] != r1.json()["id"]


def test_dedup_cache_ttl_expiry_with_injectable_clock():
    clock = {"t": 0.0}
    cache = IngestDedupCache(ttl_seconds=60, now_fn=lambda: clock["t"])
    key = compute_dedup_key("test", "tool", {"a": 1}, {"b": 2})

    hit, _ = cache.get(key)
    assert hit is False

    cache.put(key, "fact-1")
    hit, fact_id = cache.get(key)
    assert hit is True
    assert fact_id == "fact-1"

    clock["t"] += 59
    hit, fact_id = cache.get(key)
    assert hit is True

    clock["t"] += 2  # total 61s elapsed
    hit, fact_id = cache.get(key)
    assert hit is False
