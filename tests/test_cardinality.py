"""Cardinality ONE write (same/higher rank), cardinality ONE write (lower
rank)."""


async def test_cardinality_overwrite_same_or_higher_rank(client):
    r1 = await client.post(
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
    assert r1.status_code == 200
    id1 = r1.json()["id"]

    r2 = await client.post(
        "/memory/store",
        json={
            "entity": "gateway",
            "attribute": "port",
            "value": "9999",
            "content": "Gateway actually runs on port 9999",
            "provenance": "user-stated",
            "source_channel": "api_store",
            "cardinality": "ONE",
        },
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["status"] == "ok"
    id2 = body2["id"]
    assert id2 != id1

    qr = await client.post(
        "/memory/query",
        json={"query": "what port does the gateway run on", "top_k": 5},
    )
    body = qr.json()
    assert body["status"] == "ok"
    ids_returned = {item["id"] for item in body["results"]}
    assert id2 in ids_returned
    assert id1 not in ids_returned


async def test_cardinality_conflict_deferred_on_lower_rank(client):
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
    id1 = r1.json()["id"]

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
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["status"] == "pending"
    assert body2["error"]["code"] == "CARDINALITY_CONFLICT_PENDING"
    pending_id = body2["id"]
    assert pending_id != id1

    qr = await client.post(
        "/memory/query",
        json={"query": "what port does the gateway run on", "top_k": 5},
    )
    body = qr.json()
    ids_returned = {item["id"] for item in body["results"]}
    assert id1 in ids_returned
    assert pending_id not in ids_returned
