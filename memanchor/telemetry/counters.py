"""In-memory telemetry counters: request counts/latency and cumulative
cardinality/validator/contradiction event counts. Ephemeral, per-process --
resets on restart, same precedent as `IngestDedupCache`. A persisted stats
store is a reasonable future enhancement, out of scope here.
"""

from __future__ import annotations


class TelemetryCounters:
    def __init__(self) -> None:
        self._event_counts: dict[str, int] = {}
        self._latency_count: dict[str, int] = {}
        self._latency_sum_ms: dict[str, float] = {}

    def increment(self, event: str, by: int = 1) -> None:
        self._event_counts[event] = self._event_counts.get(event, 0) + by

    def get_event_count(self, event: str) -> int:
        return self._event_counts.get(event, 0)

    def record_latency(self, endpoint: str, latency_ms: float) -> None:
        self._latency_count[endpoint] = self._latency_count.get(endpoint, 0) + 1
        self._latency_sum_ms[endpoint] = self._latency_sum_ms.get(endpoint, 0.0) + latency_ms

    def get_request_count(self, endpoint: str) -> int:
        return self._latency_count.get(endpoint, 0)

    def get_avg_latency_ms(self, endpoint: str) -> float | None:
        count = self._latency_count.get(endpoint, 0)
        if count == 0:
            return None
        return self._latency_sum_ms[endpoint] / count
