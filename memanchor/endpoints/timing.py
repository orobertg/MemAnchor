"""Shared per-route latency instrumentation.

Extracted from the timing block that was duplicated verbatim across every
instrumented route (memory_store/memory_query/memory_resolve/observer_ingest)
-- one helper, all four call sites.
"""

from __future__ import annotations

import time
from typing import Awaitable, TypeVar

from memanchor.telemetry.counters import TelemetryCounters

T = TypeVar("T")


async def timed(telemetry: TelemetryCounters, endpoint: str, coro: Awaitable[T]) -> T:
    start = time.perf_counter()
    try:
        return await coro
    finally:
        telemetry.record_latency(endpoint, (time.perf_counter() - start) * 1000)
