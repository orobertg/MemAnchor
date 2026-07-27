"""Provenance guardrail -- inferred + cardinality ONE rejected without
force_provenance."""


async def test_inferred_cardinality_one_rejected_without_force(client):
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
        },
    )
    assert r.status_code == 400
    body = r.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "PROVENANCE_REJECTED"


async def test_inferred_cardinality_one_allowed_with_force_provenance(client):
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
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
