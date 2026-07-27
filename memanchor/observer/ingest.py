"""Active ingest support: dedup key computation and the in-memory dedup
cache POST /observer/ingest uses.

In-memory/per-process, resetting on restart -- a deliberate simplification
for a 60-second dedup window; not worth a persisted table.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Callable

DEFAULT_TTL_SECONDS = 60.0

_MISSING = object()


def compute_dedup_key(adapter: str, tool_name: str, inputs: dict, outputs: dict) -> str:
    """Dedup key = (adapter, tool_name, inputs_hash, outputs_hash), collapsed
    here into one hash over the canonical (sorted-key) JSON of all four."""
    canonical = json.dumps(
        {"adapter": adapter, "tool_name": tool_name, "inputs": inputs, "outputs": outputs},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class IngestDedupCache:
    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        now_fn: Callable[[], float] = time.time,
    ):
        self._ttl_seconds = ttl_seconds
        self._now_fn = now_fn
        self._entries: dict[str, tuple[float, str | None]] = {}

    def get(self, key: str) -> tuple[bool, str | None]:
        """Returns (hit, fact_id). An expired entry is purged and treated as
        a miss. `fact_id` may legitimately be None on a hit -- a duplicate
        of a call that previously matched nothing should also dedupe to
        "no match" rather than re-running on_tool_call."""
        entry = self._entries.get(key, _MISSING)
        if entry is _MISSING:
            return False, None
        timestamp, fact_id = entry
        if self._now_fn() - timestamp > self._ttl_seconds:
            del self._entries[key]
            return False, None
        return True, fact_id

    def put(self, key: str, fact_id: str | None) -> None:
        self._entries[key] = (self._now_fn(), fact_id)

    def purge_expired(self) -> int:
        """Evicts every expired entry regardless of whether it's ever looked
        up again -- get()'s lazy purge alone leaves a write-once,
        never-requeried key in memory forever."""
        now = self._now_fn()
        expired = [k for k, (timestamp, _) in self._entries.items() if now - timestamp > self._ttl_seconds]
        for k in expired:
            del self._entries[k]
        return len(expired)

    def __len__(self) -> int:
        return len(self._entries)


async def periodic_dedup_sweep(cache: IngestDedupCache, interval_seconds: float = DEFAULT_TTL_SECONDS) -> None:
    """Long-running loop re-running purge_expired every `interval_seconds`,
    mirroring write_pipeline.periodic_cleanup_sweep's shape. Intended to run
    as a cancellable background asyncio.Task for the app's lifetime --
    cancellation propagates as CancelledError, not swallowed here."""
    while True:
        await asyncio.sleep(interval_seconds)
        cache.purge_expired()
