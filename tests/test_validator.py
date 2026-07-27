"""Validator refresh, validator timeout -- plus ground-truth-wins
contradiction resolution, the max_validators_per_query cap, and the
registry register/list round-trip."""

import asyncio

from helpers import build_client


async def _store_and_register(
    client, *, entity, attribute, value, content, provenance="system-derived", tool="check_port"
):
    r = await client.post(
        "/memory/store",
        json={
            "entity": entity,
            "attribute": attribute,
            "value": value,
            "content": content,
            "provenance": provenance,
            "source_channel": "observer_ingest",
            "cardinality": "MANY",
        },
    )
    fact_id = r.json()["id"]

    await client.post(
        "/registry/register",
        json={
            "entity": entity,
            "attribute": attribute,
            "validator_tool": tool,
            "adapter": "test",
            "observed_inputs": {},
        },
    )
    return fact_id


async def test_validator_confirms_matching_value(tmp_path):
    async with build_client(tmp_path) as (client, app):
        fact_id = await _store_and_register(
            client,
            entity="gateway",
            attribute="port",
            value="8890",
            content="Gateway runs on port 8890",
        )

        async def confirm(_inputs):
            return {"value": "8890"}

        app.state.adapter.register_validator_callable("check_port", confirm)

        qr = await client.post(
            "/memory/query", json={"query": "what port does the gateway run on", "top_k": 5}
        )
        body = qr.json()
        assert body["status"] == "ok"
        assert len(body["results"]) == 1
        assert body["results"][0]["id"] == fact_id
        assert body["results"][0]["last_validated"] is not None


async def test_validator_refreshes_stale_value(tmp_path):
    async with build_client(tmp_path) as (client, app):
        old_id = await _store_and_register(
            client,
            entity="gateway",
            attribute="port",
            value="8890",
            content="Gateway runs on port 8890",
        )

        async def stale(_inputs):
            return {"value": "9999", "content": "Gateway now runs on port 9999"}

        app.state.adapter.register_validator_callable("check_port", stale)

        qr = await client.post(
            "/memory/query", json={"query": "what port does the gateway run on", "top_k": 5}
        )
        body = qr.json()
        assert body["status"] == "ok"
        assert len(body["results"]) == 1
        new_result = body["results"][0]
        assert new_result["id"] != old_id
        assert new_result["value"] == "9999"
        assert new_result["content"] == "Gateway now runs on port 9999"

        old_fact = await app.state.sqlite_store.get_fact(old_id)
        assert old_fact["status"] == "tombstoned"
        assert old_fact["superseded_by"] == new_result["id"]

        registry_row = await app.state.sqlite_store.get_entity_registry("gateway", "port")
        assert registry_row["active_fact_id"] == new_result["id"]


async def test_validator_refresh_write_failure_leaves_original_fact_active(tmp_path):
    """A failure during the refresh write (transactional_write inside
    _refresh_stale_fact) must not be signaled by returning None -- it must
    surface as VALIDATOR_FAILED/degraded, leaving the original fact untouched
    (no partial mutation) rather than raising an unhandled exception or
    silently losing the failure detail."""
    async with build_client(tmp_path) as (client, app):
        old_id = await _store_and_register(
            client,
            entity="gateway",
            attribute="port",
            value="8890",
            content="Gateway runs on port 8890",
        )

        async def stale(_inputs):
            return {"value": "9999", "content": "Gateway now runs on port 9999"}

        app.state.adapter.register_validator_callable("check_port", stale)

        async def failing_upsert(*args, **kwargs):
            raise RuntimeError("simulated chroma failure during refresh")

        app.state.chroma_store.upsert = failing_upsert

        qr = await client.post(
            "/memory/query", json={"query": "what port does the gateway run on", "top_k": 5}
        )
        body = qr.json()
        assert body["status"] == "degraded"
        assert body["error"]["code"] == "VALIDATOR_FAILED"
        # The underlying failure reason must survive into the warning message
        # -- today's null-return path discards response.error.message entirely.
        assert "simulated chroma failure during refresh" in body["error"]["message"]
        assert len(body["results"]) == 1
        assert body["results"][0]["id"] == old_id
        assert body["results"][0]["value"] == "8890"
        assert body["results"][0]["resolution"] == "pending"

        old_fact = await app.state.sqlite_store.get_fact(old_id)
        assert old_fact["status"] == "active"
        assert old_fact["superseded_by"] is None

        registry_row = await app.state.sqlite_store.get_entity_registry("gateway", "port")
        assert registry_row["active_fact_id"] == old_id


async def test_validator_timeout(tmp_path):
    async with build_client(tmp_path, validator_timeout_ms=100) as (client, app):
        fact_id = await _store_and_register(
            client,
            entity="gateway",
            attribute="port",
            value="8890",
            content="Gateway runs on port 8890",
        )

        async def hangs(_inputs):
            await asyncio.sleep(1)
            return {"value": "8890"}

        app.state.adapter.register_validator_callable("check_port", hangs)

        qr = await client.post(
            "/memory/query", json={"query": "what port does the gateway run on", "top_k": 5}
        )
        body = qr.json()
        assert body["status"] == "degraded"
        assert body["error"]["code"] == "VALIDATOR_TIMEOUT"
        assert len(body["results"]) == 1
        assert body["results"][0]["id"] == fact_id
        assert body["results"][0]["resolution"] == "pending"


async def test_validator_failed_when_callable_raises(tmp_path):
    async with build_client(tmp_path) as (client, app):
        fact_id = await _store_and_register(
            client,
            entity="gateway",
            attribute="port",
            value="8890",
            content="Gateway runs on port 8890",
        )

        async def broken(_inputs):
            raise RuntimeError("connection refused")

        app.state.adapter.register_validator_callable("check_port", broken)

        qr = await client.post(
            "/memory/query", json={"query": "what port does the gateway run on", "top_k": 5}
        )
        body = qr.json()
        assert body["status"] == "degraded"
        assert body["error"]["code"] == "VALIDATOR_FAILED"
        assert body["results"][0]["id"] == fact_id
        assert body["results"][0]["resolution"] == "pending"


async def test_validator_ground_truth_beats_conflicting_unvalidated_fact(tmp_path):
    async with build_client(tmp_path) as (client, app):
        sd_id = await _store_and_register(
            client,
            entity="gateway",
            attribute="port",
            value="8890",
            content="Gateway runs on port 8890",
        )

        async def confirm(_inputs):
            return {"value": "8890"}

        app.state.adapter.register_validator_callable("check_port", confirm)

        r = await client.post(
            "/memory/store",
            json={
                "entity": "gateway",
                "attribute": "port",
                "value": "9999",
                "content": "Gateway runs on port 9999",
                "provenance": "user-stated",
                "source_channel": "api_store",
                "cardinality": "MANY",
            },
        )
        us_id = r.json()["id"]

        qr = await client.post(
            "/memory/query", json={"query": "what port does the gateway run on", "top_k": 5}
        )
        body = qr.json()
        assert body["status"] == "ok"
        ids = {item["id"] for item in body["results"]}
        assert ids == {sd_id}
        assert us_id not in ids
        assert body["contradictions_resolved"] == 1


async def test_max_validators_per_query_cap(tmp_path):
    async with build_client(tmp_path, max_validators_per_query=1) as (client, app):
        invocations = []

        async def confirm_a(_inputs):
            invocations.append("a")
            return {"value": "8890"}

        async def confirm_b(_inputs):
            invocations.append("b")
            return {"value": "true"}

        await _store_and_register(
            client,
            entity="gateway",
            attribute="port",
            value="8890",
            content="Gateway runs on port 8890",
            tool="check_port",
        )
        await _store_and_register(
            client,
            entity="frank",
            attribute="heartbeat_ok",
            value="true",
            content="Frank's heartbeat is healthy",
            tool="check_heartbeat",
        )
        app.state.adapter.register_validator_callable("check_port", confirm_a)
        app.state.adapter.register_validator_callable("check_heartbeat", confirm_b)

        qr = await client.post("/memory/query", json={"query": "system status check", "top_k": 5})
        body = qr.json()
        assert body["status"] == "ok"
        assert len(invocations) == 1
        assert len(body["results"]) == 2


async def test_revalidate_candidates_batches_validator_lookup(tmp_path):
    """revalidate_candidates must batch-fetch validator rows for the whole
    candidate set (mirroring get_facts_by_ids/get_decay_metrics_by_ids in
    query_pipeline.py) instead of one get_validator() query per system-derived
    candidate."""
    async with build_client(tmp_path) as (client, app):
        await _store_and_register(
            client,
            entity="gateway",
            attribute="port",
            value="8890",
            content="Gateway runs on port 8890",
            tool="check_port",
        )
        await _store_and_register(
            client,
            entity="frank",
            attribute="heartbeat_ok",
            value="true",
            content="Frank's heartbeat is healthy",
            tool="check_heartbeat",
        )

        async def confirm_a(_inputs):
            return {"value": "8890"}

        async def confirm_b(_inputs):
            return {"value": "true"}

        app.state.adapter.register_validator_callable("check_port", confirm_a)
        app.state.adapter.register_validator_callable("check_heartbeat", confirm_b)

        batch_calls = []
        original = app.state.sqlite_store.get_validators_by_keys

        async def spy(keys):
            batch_calls.append(sorted(keys))
            return await original(keys)

        app.state.sqlite_store.get_validators_by_keys = spy

        qr = await client.post("/memory/query", json={"query": "system status check", "top_k": 5})
        body = qr.json()
        assert body["status"] == "ok"
        assert len(body["results"]) == 2

        assert len(batch_calls) == 1
        assert batch_calls[0] == sorted([("gateway", "port"), ("frank", "heartbeat_ok")])


async def test_registry_register_and_list(client):
    r = await client.post(
        "/registry/register",
        json={
            "entity": "gateway",
            "attribute": "port",
            "validator_tool": "check_gateway_port",
            "adapter": "test",
            "observed_inputs": {"service": "gateway"},
        },
    )
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "entity": "gateway",
        "attribute": "port",
        "validator_tool": "check_gateway_port",
    }

    listing = await client.get("/registry")
    body = listing.json()
    assert len(body["validators"]) == 1
    entry = body["validators"][0]
    assert entry["entity"] == "gateway"
    assert entry["attribute"] == "port"
    assert entry["validator_tool"] == "check_gateway_port"
    assert entry["observed_inputs"] == {"service": "gateway"}
    assert entry["callable_registered"] is False
