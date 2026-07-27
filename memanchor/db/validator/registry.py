"""In-process resolution of validator tool name -> invokable callable.

`GenericAdapter` (and any other `MemAnchorAdapter` implementation) composes
one of these to back `get_validator(tool_name)` / `register_validator_callable`.
There is no way for an HTTP call to hand over an actual invokable tool --
only a name -- so `POST /registry/register` can only persist the *pairing*
(which tool is responsible for which entity+attribute, stored in the
`validators` SQLite table). Actually invoking that tool requires a callable
registered here, in-process, by whoever embeds MemAnchor (or by tests).
"""

from __future__ import annotations

from typing import Awaitable, Callable

ValidatorCallable = Callable[[dict], Awaitable[dict]]


class ValidatorCallableRegistry:
    def __init__(self) -> None:
        self._callables: dict[str, ValidatorCallable] = {}

    def register(self, tool_name: str, fn: ValidatorCallable) -> None:
        self._callables[tool_name] = fn

    def get(self, tool_name: str) -> ValidatorCallable | None:
        return self._callables.get(tool_name)
