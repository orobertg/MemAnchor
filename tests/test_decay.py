"""Decay weighting: the pure formula/filter in isolation, plus
query-pipeline integration for bookkeeping (retrieval_count / miss_count)
and the refresh-exclusion rule."""

import math

import pytest

from helpers import build_client
from memanchor.domain.decay import apply_decay_weighting, effective_confidence


def test_effective_confidence_formula():
    assert effective_confidence(0.95, 0, 10) == 0.95
    assert effective_confidence(0.95, 10, 10) == 0.95 * math.exp(-1)


def test_apply_decay_weighting_drops_below_threshold():
    candidates = [{"id": "a", "confidence": 0.95}]
    decay_metrics_by_id = {"a": {"miss_count": 30}}
    result = apply_decay_weighting(candidates, decay_metrics_by_id, decay_halflife=10, min_confidence=0.5)
    assert result == []


def test_apply_decay_weighting_keeps_and_rewrites_confidence_above_threshold():
    candidates = [{"id": "a", "confidence": 0.95}]
    decay_metrics_by_id = {"a": {"miss_count": 2}}
    result = apply_decay_weighting(candidates, decay_metrics_by_id, decay_halflife=10, min_confidence=0.5)
    assert len(result) == 1
    assert result[0]["confidence"] == 0.95 * math.exp(-0.2)


def test_apply_decay_weighting_defaults_missing_metrics_to_zero_misses():
    candidates = [{"id": "a", "confidence": 0.95}]
    result = apply_decay_weighting(candidates, {}, decay_halflife=10, min_confidence=0.0)
    assert result[0]["confidence"] == 0.95


async def test_query_confidence_reflects_preseeded_misses(tmp_path):
    async with build_client(tmp_path) as (client, app):
        r = await client.post(
            "/memory/store",
            json={
                "entity": "widget",
                "attribute": "status",
                "value": "ok",
                "content": "Widget status is ok",
                "provenance": "inferred",
                "source_channel": "llm_extraction",
            },
        )
        fact_id = r.json()["id"]

        for _ in range(10):
            await app.state.sqlite_store.increment_miss_count(fact_id)

        qr = await client.post("/memory/query", json={"query": "widget status", "top_k": 5})
        body = qr.json()
        assert body["status"] == "ok"
        result = body["results"][0]
        assert result["id"] == fact_id
        # base confidence for inferred with no provenance_confidence override is 0.70
        expected = 0.70 * math.exp(-10 / 10)
        assert result["confidence"] == pytest.approx(expected)


async def test_query_drops_fact_once_decayed_below_min_confidence(tmp_path):
    async with build_client(tmp_path) as (client, app):
        r = await client.post(
            "/memory/store",
            json={
                "entity": "widget",
                "attribute": "status",
                "value": "ok",
                "content": "Widget status is ok",
                "provenance": "inferred",
                "source_channel": "llm_extraction",
            },
        )
        fact_id = r.json()["id"]

        for _ in range(30):
            await app.state.sqlite_store.increment_miss_count(fact_id)
        # 0.70 * exp(-3) ~= 0.035, well under 0.1

        qr = await client.post(
            "/memory/query",
            json={"query": "widget status", "top_k": 5, "min_confidence": 0.1},
        )
        assert qr.json()["results"] == []


async def test_query_bookkeeping_for_provenance_priority_contradiction(tmp_path):
    async with build_client(tmp_path, contradiction_similarity_threshold=0.5) as (client, app):
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
        loser_id = r1.json()["id"]

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
        winner_id = r2.json()["id"]

        for _ in range(3):
            qr = await client.post(
                "/memory/query",
                json={"query": "what port does the gateway run on", "top_k": 5},
            )
            ids = {item["id"] for item in qr.json()["results"]}
            assert ids == {winner_id}

        metrics = await app.state.sqlite_store.get_decay_metrics_by_ids([loser_id, winner_id])
        assert metrics[loser_id]["miss_count"] == 3
        assert metrics[loser_id]["retrieval_count"] == 0
        assert metrics[winner_id]["retrieval_count"] == 3
        assert metrics[winner_id]["miss_count"] == 0


async def test_decay_bookkeeping_excludes_validator_refreshed_fact(tmp_path):
    async with build_client(tmp_path) as (client, app):
        r = await client.post(
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
        old_id = r.json()["id"]

        await client.post(
            "/registry/register",
            json={
                "entity": "gateway",
                "attribute": "port",
                "validator_tool": "check_port",
                "adapter": "test",
                "observed_inputs": {},
            },
        )

        async def stale(_inputs):
            return {"value": "9999"}

        app.state.adapter.register_validator_callable("check_port", stale)

        qr = await client.post(
            "/memory/query", json={"query": "what port does the gateway run on", "top_k": 5}
        )
        new_id = qr.json()["results"][0]["id"]
        assert new_id != old_id

        metrics = await app.state.sqlite_store.get_decay_metrics_by_ids([old_id, new_id])
        assert old_id not in metrics
        assert new_id not in metrics
