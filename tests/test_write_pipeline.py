"""WriteIntent parameter object -- transactional_write takes one WriteIntent
(fact/cardinality/tombstone_id) instead of three loose params.
Field-for-field regression: every value passed in must round-trip through
SQLite + the entity registry unchanged."""

from helpers import build_client
from memanchor.application.write_pipeline import WriteIntent, transactional_write
from memanchor.telemetry.counters import TelemetryCounters


async def test_transactional_write_via_intent_round_trips_every_field(tmp_path):
    async with build_client(tmp_path) as (_client, app):
        fact = {
            "id": "fact-1",
            "vector_id": "fact-1",
            "entity": "gateway",
            "attribute": "port",
            "value": "8890",
            "content": "Gateway runs on port 8890",
            "provenance": "system-derived",
            "source_channel": "observer_ingest",
            "provenance_confidence": 1.0,
            "confidence": 1.0,
            "created_at": "2026-07-25T00:00:00.000000Z",
        }
        intent = WriteIntent(fact=fact, cardinality="ONE", tombstone_id=None)

        response = await transactional_write(
            app.state.sqlite_store, app.state.chroma_store, intent, TelemetryCounters()
        )
        assert response.status == "ok"
        assert response.id == "fact-1"

        stored = await app.state.sqlite_store.get_fact("fact-1")
        for key, value in fact.items():
            if key == "status":
                continue  # transactional_write legitimately transitions this
            assert stored[key] == value, key
        assert stored["status"] == "active"

        registry_row = await app.state.sqlite_store.get_entity_registry("gateway", "port")
        assert registry_row["cardinality"] == "ONE"
        assert registry_row["active_fact_id"] == "fact-1"


async def test_transactional_write_via_intent_tombstones_prior_fact(tmp_path):
    async with build_client(tmp_path) as (_client, app):
        old_fact = {
            "id": "fact-old",
            "vector_id": "fact-old",
            "entity": "gateway",
            "attribute": "port",
            "value": "1111",
            "content": "old",
            "provenance": "system-derived",
            "source_channel": "observer_ingest",
            "provenance_confidence": 1.0,
            "confidence": 1.0,
            "created_at": "2026-07-25T00:00:00.000000Z",
        }
        await transactional_write(
            app.state.sqlite_store,
            app.state.chroma_store,
            WriteIntent(fact=old_fact, cardinality="ONE", tombstone_id=None),
            TelemetryCounters(),
        )

        new_fact = {**old_fact, "id": "fact-new", "vector_id": "fact-new", "value": "2222"}
        await transactional_write(
            app.state.sqlite_store,
            app.state.chroma_store,
            WriteIntent(fact=new_fact, cardinality="ONE", tombstone_id="fact-old"),
            TelemetryCounters(),
        )

        old_row = await app.state.sqlite_store.get_fact("fact-old")
        assert old_row["status"] == "tombstoned"
        assert old_row["superseded_by"] == "fact-new"

        registry_row = await app.state.sqlite_store.get_entity_registry("gateway", "port")
        assert registry_row["active_fact_id"] == "fact-new"
