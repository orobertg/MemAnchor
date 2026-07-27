"""Generic reference adapter -- no framework-specific code, consistent with
MemAnchor's host-anywhere goal. Extracts exactly one FactCandidate per tool
call, from outputs already shaped as {entity_field, attribute_field,
value_field} -- framework-specific multi-attribute output parsing for a
tool whose result isn't already in that shape is out of scope for this
generic adapter; write a framework-specific adapter for that case.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from memanchor.application.write_pipeline import store_fact
from memanchor.contracts.adapter import FactShapeDefinition, MemAnchorAdapter, ToolCallWriteFailed
from memanchor.db.chroma.store import ChromaStore
from memanchor.db.sqlite.store import SqliteStore
from memanchor.db.validator.registry import ValidatorCallableRegistry
from memanchor.models import StoreRequest
from memanchor.telemetry.counters import TelemetryCounters


class GenericAdapter(MemAnchorAdapter):
    def __init__(
        self,
        sqlite_store: SqliteStore,
        chroma_store: ChromaStore,
        fact_shape: FactShapeDefinition,
        telemetry: TelemetryCounters | None = None,
        name: str = "generic",
    ):
        self.sqlite_store = sqlite_store
        self.chroma_store = chroma_store
        self.fact_shape = fact_shape
        self.telemetry = telemetry or TelemetryCounters()
        self.name = name
        self._callables = ValidatorCallableRegistry()

    def get_fact_shape(self) -> FactShapeDefinition:
        return self.fact_shape

    async def on_tool_call(
        self, tool_name: str, inputs: dict, outputs: dict, source_channel: str
    ) -> str | None:
        shape = self.fact_shape
        entity = outputs.get(shape.entity_field)
        attribute = outputs.get(shape.attribute_field)
        value = outputs.get(shape.value_field)
        if entity is None or attribute is None or value is None:
            return None  # no match -- not an error, just nothing to write

        attr_def = next((a for a in shape.attributes if a.name == attribute), None)
        cardinality = attr_def.cardinality if attr_def else "MANY"

        req = StoreRequest(
            entity=str(entity),
            attribute=str(attribute),
            value=str(value),
            content=f"{entity} {attribute} is {value}",
            provenance="system-derived",
            source_channel=source_channel,
            cardinality=cardinality,
        )
        response = await store_fact(self.sqlite_store, self.chroma_store, req, self.telemetry)
        if response.status in ("ok", "pending"):
            # Pair the originating tool as validator for
            # system-derived facts, regardless of whether this particular
            # write landed active or was deferred by cardinality -- the
            # entity+attribute key is still the one this tool is
            # authoritative for.
            await self.sqlite_store.register_validator(
                str(entity), str(attribute), tool_name, self.name, inputs
            )
            return response.id
        # response.status == "error": outputs matched the fact shape but
        # the write itself failed -- distinct from "nothing to write", so
        # this must not collapse into the same None.
        raise ToolCallWriteFailed(
            response.error.code if response.error else "WRITE_ROLLBACK",
            response.error.message if response.error else "unknown write failure",
        )

    def get_validator(self, tool_name: str) -> Callable[..., Awaitable[dict]] | None:
        return self._callables.get(tool_name)

    def register_validator_callable(self, tool_name: str, fn: Callable[[dict], Awaitable[dict]]) -> None:
        self._callables.register(tool_name, fn)
