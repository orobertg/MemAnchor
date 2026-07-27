"""Passive MCP observer: a real in-memory MCP client<->server session
(mcp.shared.memory), proving the observer doesn't intercept or modify the
tool call while still observing it (fact + validator pairing written)."""

import json

from mcp.shared.memory import create_connected_server_and_client_session

from helpers import build_client
from memanchor.observer.mcp_observer import ObservedTool, create_observed_server


def _gateway_port_tool():
    async def check_gateway_port(service: str) -> dict:
        return {"entity": "gateway", "attribute": "port", "value": "8890"}

    return ObservedTool(
        name="check_gateway_port",
        description="Checks the gateway's listening port",
        input_schema={
            "type": "object",
            "properties": {"service": {"type": "string"}},
            "required": ["service"],
        },
        fn=check_gateway_port,
    )


async def test_mcp_observer_writes_fact_without_altering_tool_result(tmp_path):
    async with build_client(tmp_path) as (_client, app):
        server = create_observed_server(
            "memanchor-test-observer", app.state.adapter, [_gateway_port_tool()]
        )

        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool("check_gateway_port", {"service": "gateway"})

        assert not result.isError
        returned = json.loads(result.content[0].text)
        assert returned == {"entity": "gateway", "attribute": "port", "value": "8890"}

        registry_row = await app.state.sqlite_store.get_entity_registry("gateway", "port")
        fact = await app.state.sqlite_store.get_fact(registry_row["active_fact_id"])
        assert fact["value"] == "8890"
        assert fact["provenance"] == "system-derived"

        validator_row = await app.state.sqlite_store.get_validator("gateway", "port")
        assert validator_row["validator_tool"] == "check_gateway_port"
        assert validator_row["observed_inputs"] == {"service": "gateway"}


async def test_mcp_observer_write_failure_does_not_affect_tool_result(tmp_path):
    # on_tool_call raises ToolCallWriteFailed on a real write failure
    # (instead of a silent None) -- the observer is fire-and-forget
    # by design (see module docstring), so that must not surface as a
    # tool-call error or otherwise change the wrapped tool's own result.
    async with build_client(tmp_path) as (_client, app):
        async def failing_upsert(*args, **kwargs):
            raise RuntimeError("simulated chroma failure")

        app.state.chroma_store.upsert = failing_upsert

        server = create_observed_server(
            "memanchor-test-observer", app.state.adapter, [_gateway_port_tool()]
        )

        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool("check_gateway_port", {"service": "gateway"})

        assert not result.isError
        returned = json.loads(result.content[0].text)
        assert returned == {"entity": "gateway", "attribute": "port", "value": "8890"}


async def test_mcp_observer_lists_registered_tools(tmp_path):
    async with build_client(tmp_path) as (_client, app):
        server = create_observed_server(
            "memanchor-test-observer", app.state.adapter, [_gateway_port_tool()]
        )

        async with create_connected_server_and_client_session(server) as session:
            listing = await session.list_tools()

        assert [t.name for t in listing.tools] == ["check_gateway_port"]
