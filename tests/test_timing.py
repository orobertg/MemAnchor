"""endpoints/timing.py: the shared timed() helper extracted from the 4
duplicated per-route timing blocks."""

import pytest

from memanchor.endpoints.timing import timed
from memanchor.telemetry.counters import TelemetryCounters


async def test_timed_returns_the_wrapped_coroutines_result_unchanged():
    telemetry = TelemetryCounters()

    async def work():
        return {"status": "ok", "id": "abc"}

    result = await timed(telemetry, "memory_store", work())

    assert result == {"status": "ok", "id": "abc"}


async def test_timed_records_latency_under_the_given_metric_name():
    telemetry = TelemetryCounters()

    async def work():
        return None

    await timed(telemetry, "memory_query", work())

    assert telemetry.get_request_count("memory_query") == 1
    assert telemetry.get_avg_latency_ms("memory_query") is not None
    assert telemetry.get_avg_latency_ms("memory_query") >= 0
    assert telemetry.get_request_count("memory_store") == 0


async def test_timed_records_latency_even_when_the_wrapped_call_raises():
    telemetry = TelemetryCounters()

    async def failing():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await timed(telemetry, "memory_resolve", failing())

    assert telemetry.get_request_count("memory_resolve") == 1
