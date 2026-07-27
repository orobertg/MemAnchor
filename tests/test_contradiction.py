"""Semantic contradiction (resolvable) and semantic contradiction
(unresolvable). Uses a lowered contradiction_similarity_threshold to keep
the test independent of exact MiniLM embedding magnitudes -- the default
0.92 is itself unvalidated, which is why calibrate_threshold.py exists for
operators to pick a real value."""

from helpers import build_client


async def test_semantic_contradiction_resolvable_by_provenance_priority(tmp_path):
    async with build_client(tmp_path, contradiction_similarity_threshold=0.5) as (client, _app):
        r1 = await client.post(
            "/memory/store",
            json={
                "entity": "gateway",
                "attribute": "port",
                "value": "8890",
                "content": "Gateway runs on port 8890",
                "provenance": "user-stated",
                "source_channel": "api_store",
                "cardinality": "MANY",
            },
        )
        id1 = r1.json()["id"]

        r2 = await client.post(
            "/memory/store",
            json={
                "entity": "gateway",
                "attribute": "port",
                "value": "9999",
                "content": "Gateway runs on port 9999",
                "provenance": "system-derived",
                "source_channel": "observer_ingest",
                "cardinality": "MANY",
            },
        )
        id2 = r2.json()["id"]

        qr = await client.post(
            "/memory/query",
            json={"query": "what port does the gateway run on", "top_k": 5},
        )
        body = qr.json()
        assert body["status"] == "ok"
        assert body["contradictions_resolved"] == 1
        ids = {item["id"] for item in body["results"]}
        assert id2 in ids
        assert id1 not in ids


async def test_semantic_contradiction_unresolvable_same_provenance_rank(tmp_path):
    async with build_client(tmp_path, contradiction_similarity_threshold=0.5) as (client, _app):
        r1 = await client.post(
            "/memory/store",
            json={
                "entity": "gateway",
                "attribute": "port",
                "value": "8890",
                "content": "Gateway runs on port 8890",
                "provenance": "system-derived",
                "source_channel": "observer_ingest",
                "cardinality": "MANY",
            },
        )
        id1 = r1.json()["id"]

        r2 = await client.post(
            "/memory/store",
            json={
                "entity": "gateway",
                "attribute": "port",
                "value": "9999",
                "content": "Gateway runs on port 9999",
                "provenance": "system-derived",
                "source_channel": "observer_ingest",
                "cardinality": "MANY",
            },
        )
        id2 = r2.json()["id"]

        qr = await client.post(
            "/memory/query",
            json={"query": "what port does the gateway run on", "top_k": 5},
        )
        body = qr.json()
        assert body["status"] == "unresolved"
        assert body["results"] == []
        assert body["error"]["code"] == "CONFLICT_UNRESOLVABLE"
        assert set(body["error"]["conflicting_ids"]) == {id1, id2}
