"""Task 09: bound IngestDedupCache growth. Entries are otherwise only
pruned lazily on get() of the exact same key -- a key written once and
never re-queried would live forever. A periodic sweep evicts all expired
entries regardless of whether anyone looks them up again."""

import asyncio
import contextlib

from helpers import build_client

from memanchor.observer.ingest import IngestDedupCache, compute_dedup_key, periodic_dedup_sweep


def test_purge_expired_evicts_never_requeried_key():
    clock = {"t": 0.0}
    cache = IngestDedupCache(ttl_seconds=60, now_fn=lambda: clock["t"])
    key = compute_dedup_key("test", "tool", {"a": 1}, {"b": 2})
    cache.put(key, "fact-1")
    assert len(cache) == 1

    clock["t"] += 61  # expired, but nobody has called get() on it
    removed = cache.purge_expired()

    assert removed == 1
    assert len(cache) == 0


def test_purge_expired_leaves_live_entries_alone():
    clock = {"t": 0.0}
    cache = IngestDedupCache(ttl_seconds=60, now_fn=lambda: clock["t"])
    key = compute_dedup_key("test", "tool", {"a": 1}, {"b": 2})
    cache.put(key, "fact-1")

    clock["t"] += 30  # still live
    removed = cache.purge_expired()

    assert removed == 0
    assert len(cache) == 1


async def test_periodic_dedup_sweep_evicts_without_manual_get(tmp_path):
    clock = {"t": 0.0}
    cache = IngestDedupCache(ttl_seconds=1, now_fn=lambda: clock["t"])
    key = compute_dedup_key("test", "tool", {"a": 1}, {"b": 2})
    cache.put(key, "fact-1")
    clock["t"] += 2  # expired

    sweep_task = asyncio.create_task(periodic_dedup_sweep(cache, interval_seconds=0.05))
    try:
        await asyncio.sleep(0.2)
        assert len(cache) == 0
    finally:
        sweep_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweep_task


async def test_app_lifespan_launches_and_cancels_dedup_sweep(tmp_path):
    async with build_client(tmp_path, ingest_dedup_sweep_interval_seconds=0.05) as (client, app):
        sweep_task = app.state.ingest_dedup_sweep_task
        assert not sweep_task.done()
        await asyncio.sleep(0.15)

    assert sweep_task.cancelled()
