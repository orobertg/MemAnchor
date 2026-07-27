"""Adapter interface. MemAnchor core is framework agnostic; the adapter is
the only framework-aware component. The interface is meant to stay stable
across versions; new methods should be additive only.

`MemAnchorAdapter` is a plain class with `NotImplementedError` bodies rather
than `abc.ABC`, so a subclass overriding only the methods it needs doesn't
require declaring itself abstract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Literal


@dataclass
class AttributeDefinition:
    name: str
    cardinality: Literal["ONE", "MANY"] = "MANY"
    allow_inferred_provenance: bool = False
    allow_auto_resolve_cardinality_one: bool = False


@dataclass
class FactShapeDefinition:
    entity_field: str = "entity"
    attribute_field: str = "attribute"
    value_field: str = "value"
    attributes: list[AttributeDefinition] = field(default_factory=list)


@dataclass
class FactCandidate:
    entity: str
    attribute: str
    value: str
    content: str | None = None


class ToolCallWriteFailed(Exception):
    """Raised by on_tool_call when outputs matched the fact shape but the
    resulting write itself failed (StoreResponse.status == "error") --
    kept distinct from the None return, which means "outputs didn't match
    the fact shape, nothing to write". Carries the underlying error's
    code/message through so a caller (e.g. POST /observer/ingest) can
    report the real failure instead of a silent success."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class MemAnchorAdapter:
    def get_fact_shape(self) -> FactShapeDefinition:
        """Definition of registerable system facts in this framework."""
        raise NotImplementedError

    def on_tool_call(
        self,
        tool_name: str,
        inputs: dict,
        outputs: dict,
        source_channel: str,
    ) -> str | None:
        """
        Called by observer or ingest handler for each tool call.
        Applies fact shape definition and triggers store if match.

        Returns the written fact's id, or None if outputs didn't match the
        fact shape -- POST /observer/ingest reports that id back in its own
        response, and reuses it as the cached id on a dedup hit.

        Raises ToolCallWriteFailed if outputs matched the fact shape but
        the write itself failed -- that case must not be reported the same
        way as the no-match None.
        """
        raise NotImplementedError

    def get_validator(self, tool_name: str) -> Callable[..., Awaitable[dict]]:
        """Returns the async callable validator for a named tool."""
        raise NotImplementedError


def load_fact_shape(path: str) -> FactShapeDefinition:
    """Loads the JSON-serializable fact shape schema (see
    fact_shape.example.json for the format). Returns a default (no declared
    attributes, everything implicitly cardinality MANY) if the file doesn't
    exist."""
    p = Path(path)
    if not p.exists():
        return FactShapeDefinition()
    with p.open() as f:
        raw = json.load(f)
    attributes = [AttributeDefinition(**a) for a in raw.get("attributes", [])]
    return FactShapeDefinition(
        entity_field=raw.get("entity_field", "entity"),
        attribute_field=raw.get("attribute_field", "attribute"),
        value_field=raw.get("value_field", "value"),
        attributes=attributes,
    )
