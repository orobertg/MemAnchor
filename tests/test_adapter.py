"""Adapter interface: GenericAdapter's fact-shape matching, on_tool_call's
write + validator pairing, cardinality passthrough, and
get_validator/register_validator_callable -- plus one end-to-end proof that
query-time validator revalidation still works through the adapter."""

import pytest

from helpers import build_client
from memanchor.adapters.generic.adapter import GenericAdapter
from memanchor.contracts.adapter import AttributeDefinition, FactShapeDefinition, ToolCallWriteFailed


def _install_adapter(app, fact_shape):
    app.state.adapter = GenericAdapter(app.state.sqlite_store, app.state.chroma_store, fact_shape)


async def test_get_fact_shape_returns_configured_shape(tmp_path):
    async with build_client(tmp_path) as (_client, app):
        shape = FactShapeDefinition(attributes=[AttributeDefinition(name="port", cardinality="ONE")])
        _install_adapter(app, shape)

        assert app.state.adapter.get_fact_shape() is shape


async def test_on_tool_call_matching_outputs_writes_fact_and_pairs_validator(tmp_path):
    # Checks the write + pairing directly via the store, not through
    # /memory/query -- querying would trigger validator revalidation,
    # which is a separate concern covered by the end-to-end test below.
    # No callable is registered here, so a query would correctly
    # come back "degraded"/VALIDATOR_FAILED; that's not what this test is
    # about.
    async with build_client(tmp_path) as (_client, app):
        _install_adapter(app, FactShapeDefinition())

        await app.state.adapter.on_tool_call(
            tool_name="check_gateway_port",
            inputs={"service": "gateway"},
            outputs={"entity": "gateway", "attribute": "port", "value": "8890"},
            source_channel="observer_ingest",
        )

        registry_row = await app.state.sqlite_store.get_entity_registry("gateway", "port")
        fact = await app.state.sqlite_store.get_fact(registry_row["active_fact_id"])
        assert fact["entity"] == "gateway"
        assert fact["attribute"] == "port"
        assert fact["value"] == "8890"
        assert fact["provenance"] == "system-derived"
        assert fact["status"] == "active"

        validator_row = await app.state.sqlite_store.get_validator("gateway", "port")
        assert validator_row["validator_tool"] == "check_gateway_port"
        assert validator_row["observed_inputs"] == {"service": "gateway"}


async def test_on_tool_call_missing_field_is_a_noop(tmp_path):
    async with build_client(tmp_path) as (client, app):
        _install_adapter(app, FactShapeDefinition())

        await app.state.adapter.on_tool_call(
            tool_name="check_gateway_port",
            inputs={},
            outputs={"entity": "gateway", "attribute": "port"},  # no "value"
            source_channel="observer_ingest",
        )

        qr = await client.post(
            "/memory/query", json={"query": "what port does the gateway run on", "top_k": 5}
        )
        assert qr.json()["results"] == []

        validator_row = await app.state.sqlite_store.get_validator("gateway", "port")
        assert validator_row is None


async def test_on_tool_call_cardinality_one_triggers_overwrite(tmp_path):
    async with build_client(tmp_path) as (client, app):
        shape = FactShapeDefinition(attributes=[AttributeDefinition(name="port", cardinality="ONE")])
        _install_adapter(app, shape)

        await app.state.adapter.on_tool_call(
            tool_name="check_gateway_port",
            inputs={},
            outputs={"entity": "gateway", "attribute": "port", "value": "8890"},
            source_channel="observer_ingest",
        )
        await app.state.adapter.on_tool_call(
            tool_name="check_gateway_port",
            inputs={},
            outputs={"entity": "gateway", "attribute": "port", "value": "9999"},
            source_channel="observer_ingest",
        )

        qr = await client.post(
            "/memory/query", json={"query": "what port does the gateway run on", "top_k": 5}
        )
        body = qr.json()
        assert len(body["results"]) == 1
        assert body["results"][0]["value"] == "9999"


async def test_on_tool_call_write_failure_raises_not_none(tmp_path):
    # A real backend write failure (as opposed to outputs not matching the
    # fact shape) must not collapse into the same None the
    # genuine no-op case returns -- on_tool_call raises instead, so a
    # caller can tell "nothing to do" apart from "the write failed".
    async with build_client(tmp_path) as (_client, app):
        _install_adapter(app, FactShapeDefinition())

        async def failing_upsert(*args, **kwargs):
            raise RuntimeError("simulated chroma failure")

        app.state.chroma_store.upsert = failing_upsert

        with pytest.raises(ToolCallWriteFailed) as exc_info:
            await app.state.adapter.on_tool_call(
                tool_name="check_gateway_port",
                inputs={"service": "gateway"},
                outputs={"entity": "gateway", "attribute": "port", "value": "8890"},
                source_channel="observer_ingest",
            )
        assert exc_info.value.code == "WRITE_ROLLBACK"


async def test_get_validator_returns_none_then_registered_callable(tmp_path):
    async with build_client(tmp_path) as (_client, app):
        _install_adapter(app, FactShapeDefinition())

        assert app.state.adapter.get_validator("check_gateway_port") is None

        async def fn(_inputs):
            return {"value": "8890"}

        app.state.adapter.register_validator_callable("check_gateway_port", fn)
        assert app.state.adapter.get_validator("check_gateway_port") is fn


async def test_end_to_end_on_tool_call_then_http_query_revalidates(tmp_path):
    async with build_client(tmp_path) as (client, app):
        _install_adapter(app, FactShapeDefinition())

        await app.state.adapter.on_tool_call(
            tool_name="check_gateway_port",
            inputs={"service": "gateway"},
            outputs={"entity": "gateway", "attribute": "port", "value": "8890"},
            source_channel="observer_ingest",
        )

        async def confirm(_inputs):
            return {"value": "8890"}

        app.state.adapter.register_validator_callable("check_gateway_port", confirm)

        qr = await client.post(
            "/memory/query", json={"query": "what port does the gateway run on", "top_k": 5}
        )
        body = qr.json()
        assert body["status"] == "ok"
        assert body["results"][0]["last_validated"] is not None
