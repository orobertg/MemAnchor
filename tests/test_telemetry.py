"""Telemetry endpoints: /telemetry/stats, /telemetry/contradictions,
/telemetry/provenance-audit."""

from helpers import build_client


async def test_telemetry_stats_reflects_a_known_sequence(tmp_path):
    async with build_client(tmp_path, contradiction_similarity_threshold=0.5) as (client, app):
        # --- Cardinality overwrite ---
        await client.post(
            "/memory/store",
            json={
                "entity": "gateway",
                "attribute": "port",
                "value": "8890",
                "content": "Gateway runs on port 8890",
                "provenance": "system-derived",
                "source_channel": "observer_ingest",
                "cardinality": "ONE",
            },
        )
        await client.post(
            "/memory/store",
            json={
                "entity": "gateway",
                "attribute": "port",
                "value": "9999",
                "content": "Gateway runs on port 9999",
                "provenance": "user-stated",
                "source_channel": "api_store",
                "cardinality": "ONE",
            },
        )

        # --- Cardinality deferred + promote ---
        r = await client.post(
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
        pending_id = r.json()["id"]
        await client.post(
            "/memory/resolve",
            json={
                "action": "cardinality_conflict_resolve",
                "fact_id": pending_id,
                "resolution": "promote",
            },
        )

        # --- Validator confirm ---
        await client.post(
            "/memory/store",
            json={
                "entity": "frank",
                "attribute": "heartbeat_ok",
                "value": "true",
                "content": "Frank's heartbeat is healthy",
                "provenance": "system-derived",
                "source_channel": "observer_ingest",
                "cardinality": "MANY",
            },
        )
        await client.post(
            "/registry/register",
            json={
                "entity": "frank",
                "attribute": "heartbeat_ok",
                "validator_tool": "check_heartbeat",
                "adapter": "test",
                "observed_inputs": {},
            },
        )

        async def confirm(_inputs):
            return {"value": "true"}

        app.state.adapter.register_validator_callable("check_heartbeat", confirm)
        await client.post("/memory/query", json={"query": "is frank healthy", "top_k": 5})

        # --- Unresolved contradiction ---
        await client.post(
            "/memory/store",
            json={
                "entity": "widget",
                "attribute": "status",
                "value": "ok",
                "content": "Widget status is ok",
                "provenance": "system-derived",
                "source_channel": "observer_ingest",
                "cardinality": "MANY",
            },
        )
        await client.post(
            "/memory/store",
            json={
                "entity": "widget",
                "attribute": "status",
                "value": "broken",
                "content": "Widget status is broken",
                "provenance": "system-derived",
                "source_channel": "observer_ingest",
                "cardinality": "MANY",
            },
        )
        # top_k=2 here (not 5): with this small a corpus, a looser top_k would
        # also pull in the unrelated `frank` fact as a lower-ranked candidate,
        # triggering a second (correct, but here undesired) validator
        # re-invocation and making this test's counts non-deterministic.
        qr = await client.post("/memory/query", json={"query": "widget status", "top_k": 2})
        assert qr.json()["status"] == "unresolved"

        stats = (await client.get("/telemetry/stats")).json()

        assert stats["cardinality"] == {
            "enforced_overwrites": 1,
            "conflicts_deferred": 1,
            "conflicts_promoted": 1,
            "conflicts_discarded": 0,
        }
        assert stats["validators"] == {
            "invocations": 1,
            "confirmed": 1,
            "refreshed": 0,
            "timeouts": 0,
            "failed": 0,
        }
        assert stats["contradictions"]["unresolved_total"] == 1
        assert stats["provenance_rejected_total"] == 0
        assert stats["write_rollback_total"] == 0

        assert stats["requests"]["memory_store"]["count"] == 6
        assert stats["requests"]["memory_resolve"]["count"] == 1
        assert stats["requests"]["memory_query"]["count"] == 2
        for endpoint in ("memory_store", "memory_resolve", "memory_query"):
            assert stats["requests"][endpoint]["avg_latency_ms"] is not None
            assert stats["requests"][endpoint]["avg_latency_ms"] >= 0


async def test_telemetry_contradictions_logs_unresolved_and_decay_flags(tmp_path):
    async with build_client(tmp_path, contradiction_similarity_threshold=0.5) as (client, app):
        r1 = await client.post(
            "/memory/store",
            json={
                "entity": "widget",
                "attribute": "status",
                "value": "ok",
                "content": "Widget status is ok",
                "provenance": "system-derived",
                "source_channel": "observer_ingest",
                "cardinality": "MANY",
            },
        )
        id1 = r1.json()["id"]
        r2 = await client.post(
            "/memory/store",
            json={
                "entity": "widget",
                "attribute": "status",
                "value": "broken",
                "content": "Widget status is broken",
                "provenance": "system-derived",
                "source_channel": "observer_ingest",
                "cardinality": "MANY",
            },
        )
        id2 = r2.json()["id"]

        qr = await client.post("/memory/query", json={"query": "widget status", "top_k": 5})
        assert qr.json()["status"] == "unresolved"

        r3 = await client.post(
            "/memory/store",
            json={
                "entity": "sensor",
                "attribute": "reading",
                "value": "42",
                "content": "Sensor reading is 42",
                "provenance": "inferred",
                "source_channel": "llm_extraction",
                "cardinality": "MANY",
            },
        )
        flagged_id = r3.json()["id"]
        for _ in range(10):
            await app.state.sqlite_store.increment_miss_count(flagged_id)

        body = (await client.get("/telemetry/contradictions")).json()

        assert len(body["unresolved_contradictions"]) == 1
        entry = body["unresolved_contradictions"][0]
        assert entry["query_text"] == "widget status"
        assert set(entry["conflicting_fact_ids"]) == {id1, id2}
        assert entry["entity"] == "widget"
        assert entry["attribute"] == "status"

        flagged_ids = {c["fact_id"] for c in body["decay_flagged_candidates"]}
        assert flagged_id in flagged_ids
        flagged = next(c for c in body["decay_flagged_candidates"] if c["fact_id"] == flagged_id)
        assert flagged["miss_count"] == 10
        assert flagged["entity"] == "sensor"
        assert flagged["attribute"] == "reading"


async def test_telemetry_provenance_audit_counts_and_anomalies(client):
    await client.post(
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
    await client.post(
        "/memory/store",
        json={
            "entity": "robert",
            "attribute": "preferred_units",
            "value": "metric",
            "content": "Robert prefers metric units",
            "provenance": "user-stated",
            "source_channel": "api_store",
        },
    )
    await client.post(
        "/memory/store",
        json={
            "entity": "weather",
            "attribute": "trend",
            "value": "cooling",
            "content": "Weather seems to be cooling",
            "provenance": "inferred",
            "source_channel": "llm_extraction",
        },
    )

    r = await client.post(
        "/memory/store",
        json={
            "entity": "user",
            "attribute": "home_city",
            "value": "Seattle",
            "content": "User seems to live in Seattle",
            "provenance": "inferred",
            "source_channel": "llm_extraction",
            "cardinality": "ONE",
            "force_provenance": True,
        },
    )
    anomalous_id = r.json()["id"]

    body = (await client.get("/telemetry/provenance-audit")).json()

    counts_by_key = {(c["provenance"], c["status"]): c["count"] for c in body["counts"]}
    assert counts_by_key[("system-derived", "active")] == 1
    assert counts_by_key[("user-stated", "active")] == 1
    assert counts_by_key[("inferred", "active")] == 2

    anomalies_by_id = {a["fact_id"]: a for a in body["anomalies"]}
    assert anomalous_id in anomalies_by_id
    assert anomalies_by_id[anomalous_id]["entity"] == "user"
    assert anomalies_by_id[anomalous_id]["attribute"] == "home_city"
    assert anomalies_by_id[anomalous_id]["reason"] == "inferred provenance on cardinality-ONE attribute"
