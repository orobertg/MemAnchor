"""Cardinality promote, cardinality promote failure, cardinality promote
crash safety, cardinality discard -- plus reclassify_provenance and the
resolve-specific error paths."""

from memanchor.application.write_pipeline import cleanup_stale_pending
from helpers import build_client


async def _store_cardinality_one_pair(client):
    """Active user-stated fact + a deferred lower-rank system-derived
    conflict against it, mirroring the Phase 1 cardinality smoke test."""
    r1 = await client.post(
        "/memory/store",
        json={
            "entity": "gateway",
            "attribute": "port",
            "value": "8890",
            "content": "Gateway runs on port 8890",
            "provenance": "user-stated",
            "source_channel": "api_store",
            "cardinality": "ONE",
        },
    )
    active_id = r1.json()["id"]

    r2 = await client.post(
        "/memory/store",
        json={
            "entity": "gateway",
            "attribute": "port",
            "value": "7777",
            "content": "Monitoring detected gateway on port 7777",
            "provenance": "system-derived",
            "source_channel": "observer_ingest",
            "cardinality": "ONE",
        },
    )
    body2 = r2.json()
    assert body2["status"] == "pending"
    pending_id = body2["id"]
    return active_id, pending_id


async def test_cardinality_conflict_discard(client):
    active_id, pending_id = await _store_cardinality_one_pair(client)

    r = await client.post(
        "/memory/resolve",
        json={
            "action": "cardinality_conflict_resolve",
            "fact_id": pending_id,
            "resolution": "discard",
        },
    )
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "id": pending_id, "error": None}

    qr = await client.post(
        "/memory/query", json={"query": "what port does the gateway run on", "top_k": 5}
    )
    body = qr.json()
    ids = {item["id"] for item in body["results"]}
    assert ids == {active_id}


async def test_cardinality_conflict_promote(tmp_path):
    async with build_client(tmp_path) as (client, app):
        active_id, pending_id = await _store_cardinality_one_pair(client)

        r = await client.post(
            "/memory/resolve",
            json={
                "action": "cardinality_conflict_resolve",
                "fact_id": pending_id,
                "resolution": "promote",
            },
        )
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "id": pending_id, "error": None}

        qr = await client.post(
            "/memory/query", json={"query": "what port does the gateway run on", "top_k": 5}
        )
        body = qr.json()
        ids = {item["id"] for item in body["results"]}
        assert ids == {pending_id}

        active_fact = await app.state.sqlite_store.get_fact(active_id)
        assert active_fact["status"] == "tombstoned"
        assert active_fact["superseded_by"] == pending_id

        registry_row = await app.state.sqlite_store.get_entity_registry("gateway", "port")
        assert registry_row["active_fact_id"] == pending_id


async def test_cardinality_conflict_promote_chroma_failure(tmp_path):
    async with build_client(tmp_path) as (client, app):
        active_id, pending_id = await _store_cardinality_one_pair(client)

        async def failing_upsert(*args, **kwargs):
            raise RuntimeError("simulated chroma failure")

        app.state.chroma_store.upsert = failing_upsert

        r = await client.post(
            "/memory/resolve",
            json={
                "action": "cardinality_conflict_resolve",
                "fact_id": pending_id,
                "resolution": "promote",
            },
        )
        assert r.status_code == 500
        body = r.json()
        assert body["status"] == "error"
        assert body["error"]["code"] == "WRITE_ROLLBACK"

        active_fact = await app.state.sqlite_store.get_fact(active_id)
        assert active_fact["status"] == "active"

        pending_fact = await app.state.sqlite_store.get_fact(pending_id)
        assert pending_fact["status"] == "provenance_pending"
        assert pending_fact["conflict_type"] == "cardinality"

        registry_row = await app.state.sqlite_store.get_entity_registry("gateway", "port")
        assert registry_row["active_fact_id"] == active_id


async def test_cardinality_conflict_promote_crash_safety(tmp_path):
    async with build_client(tmp_path) as (client, app):
        active_id, pending_id = await _store_cardinality_one_pair(client)

        # Simulate a crash between promote steps 2 and 3: the row is stuck
        # at the mid-write 'pending' marker with no vector ever written.
        await app.state.sqlite_store.set_fact_status(pending_id, "pending")

        purged = await cleanup_stale_pending(
            app.state.sqlite_store, app.state.chroma_store, older_than_seconds=0
        )
        assert purged >= 1

        pending_fact = await app.state.sqlite_store.get_fact(pending_id)
        assert pending_fact is None

        active_fact = await app.state.sqlite_store.get_fact(active_id)
        assert active_fact["status"] == "active"

        registry_row = await app.state.sqlite_store.get_entity_registry("gateway", "port")
        assert registry_row["active_fact_id"] == active_id


async def test_cardinality_conflict_promote_step4_failure(tmp_path):
    """A failure after the vector is committed (step 4: tombstone old active
    + registry update + finalize) must roll the pending fact back to
    provenance_pending, matching transactional_write's rollback discipline --
    not leak a fact stuck at 'pending' forever with an orphaned vector."""
    async with build_client(tmp_path) as (client, app):
        active_id, pending_id = await _store_cardinality_one_pair(client)

        async def failing_tombstone(*args, **kwargs):
            raise RuntimeError("simulated step-4 failure")

        app.state.sqlite_store.tombstone_fact = failing_tombstone

        r = await client.post(
            "/memory/resolve",
            json={
                "action": "cardinality_conflict_resolve",
                "fact_id": pending_id,
                "resolution": "promote",
            },
        )
        assert r.status_code == 500
        body = r.json()
        assert body["status"] == "error"
        assert body["error"]["code"] == "WRITE_ROLLBACK"

        pending_fact = await app.state.sqlite_store.get_fact(pending_id)
        assert pending_fact["status"] == "provenance_pending"
        assert pending_fact["conflict_type"] == "cardinality"

        active_fact = await app.state.sqlite_store.get_fact(active_id)
        assert active_fact["status"] == "active"

        registry_row = await app.state.sqlite_store.get_entity_registry("gateway", "port")
        assert registry_row["active_fact_id"] == active_id


async def test_cleanup_stale_pending_batches_chroma_delete(tmp_path):
    """cleanup_stale_pending must issue one batched vector_store.delete(...)
    call for every stale row, not one call per row -- vector_store.delete
    already accepts a list."""
    async with build_client(tmp_path) as (client, app):
        stale_ids = []
        for i in range(2):
            r = await client.post(
                "/memory/store",
                json={
                    "entity": f"sensor{i}",
                    "attribute": "reading",
                    "value": str(i),
                    "content": f"sensor {i} reading is {i}",
                    "provenance": "system-derived",
                    "source_channel": "observer_ingest",
                },
            )
            fact_id = r.json()["id"]
            stale_ids.append(fact_id)
            # Simulate a crash mid-write: stuck at the 'pending' marker.
            await app.state.sqlite_store.set_fact_status(fact_id, "pending")

        delete_calls = []
        original_delete = app.state.chroma_store.delete

        async def spy(ids_arg):
            delete_calls.append(list(ids_arg))
            return await original_delete(ids_arg)

        app.state.chroma_store.delete = spy

        purged = await cleanup_stale_pending(
            app.state.sqlite_store, app.state.chroma_store, older_than_seconds=0
        )
        assert purged == 2

        assert len(delete_calls) == 1
        assert sorted(delete_calls[0]) == sorted(stale_ids)

        for fact_id in stale_ids:
            assert await app.state.sqlite_store.get_fact(fact_id) is None


async def test_reclassify_provenance(client):
    r1 = await client.post(
        "/memory/store",
        json={
            "entity": "frank",
            "attribute": "heartbeat_ok",
            "value": "true",
            "content": "Frank's heartbeat is healthy",
            "provenance": "system-derived",
            "source_channel": "observer_ingest",
        },
    )
    fact_id = r1.json()["id"]

    r2 = await client.post(
        "/memory/resolve",
        json={
            "action": "reclassify_provenance",
            "fact_id": fact_id,
            "new_provenance": "inferred",
            "reason": "originally misclassified by adapter",
        },
    )
    assert r2.status_code == 200
    assert r2.json() == {"status": "ok", "id": fact_id, "error": None}

    qr = await client.post("/memory/query", json={"query": "is frank healthy", "top_k": 5})
    result = qr.json()["results"][0]
    assert result["provenance"] == "inferred"


async def test_resolve_fact_not_found(client):
    r = await client.post(
        "/memory/resolve",
        json={
            "action": "cardinality_conflict_resolve",
            "fact_id": "does-not-exist",
            "resolution": "discard",
        },
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "FACT_NOT_FOUND"


async def test_resolve_invalid_state_on_active_fact(client):
    r1 = await client.post(
        "/memory/store",
        json={
            "entity": "gateway",
            "attribute": "port",
            "value": "8890",
            "content": "Gateway runs on port 8890",
            "provenance": "system-derived",
            "source_channel": "observer_ingest",
        },
    )
    fact_id = r1.json()["id"]

    r2 = await client.post(
        "/memory/resolve",
        json={
            "action": "cardinality_conflict_resolve",
            "fact_id": fact_id,
            "resolution": "discard",
        },
    )
    assert r2.status_code == 400
    assert r2.json()["error"]["code"] == "INVALID_RESOLVE_STATE"
