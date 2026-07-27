"""Passive MCP observer: wraps real MCP tool implementations so that after
each call completes, the observed
(tool_name, inputs, outputs) triple is forwarded to adapter.on_tool_call()
-- purely an observation hook. The tool's own inputs/outputs are never
intercepted or modified.

Built on the low-level mcp.server.lowlevel.Server API (list_tools/call_tool
receiving (name, arguments) generically) rather than FastMCP, because
FastMCP's decorator-based tool registration infers JSON schema from a
statically typed function signature -- which doesn't fit wrapping arbitrary
tools by name dynamically.

Observation is awaited inline, after computing the real tool's result and
before returning it -- not dispatched as a background task. This keeps the
reference implementation simple and deterministic, at the cost of adding
the observation's latency to the call. A production integration
prioritizing zero added latency could background it instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import mcp.types as types
from mcp.server.lowlevel import Server

from memanchor.contracts.adapter import MemAnchorAdapter, ToolCallWriteFailed

ToolFn = Callable[..., Awaitable[dict[str, Any]]]

DEFAULT_SOURCE_CHANNEL = "mcp_observer"


@dataclass
class ObservedTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: ToolFn


def create_observed_server(
    server_name: str,
    adapter: MemAnchorAdapter,
    tools: list[ObservedTool],
    source_channel: str = DEFAULT_SOURCE_CHANNEL,
) -> Server:
    server: Server = Server(server_name)
    tools_by_name = {t.name: t for t in tools}

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(name=t.name, description=t.description, inputSchema=t.input_schema)
            for t in tools_by_name.values()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
        arguments = arguments or {}
        tool = tools_by_name[name]
        outputs = await tool.fn(**arguments)
        try:
            await adapter.on_tool_call(name, arguments, outputs, source_channel)
        except ToolCallWriteFailed:
            # Observation is best-effort (module docstring): a write
            # failure on the observation side must never affect the
            # wrapped tool's own result.
            pass
        return [types.TextContent(type="text", text=json.dumps(outputs))]

    return server
