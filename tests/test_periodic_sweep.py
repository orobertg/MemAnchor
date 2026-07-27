"""Periodic (not just startup-only) stale-pending sweep: a fact that goes
stale mid-runtime must be recovered without a process restart."""

import asyncio
import contextlib

import pytest

from helpers import build_client

from memanchor.application.write_pipeline import periodic_cleanup_sweep


async def test_periodic_cleanup_sweep_removes_stale_facts_without_manual_call(tmp_path):
    async with build_client(tmp_path) as (client, app):
        r = await client.post(
            "/memory/store",
            json={
                "entity": "sensor",
                "attribute": "reading",
                "value": "1",
                "content": "sensor reading is 1",
                "provenance": "system-derived",
                "source_channel": "observer_ingest",
            },
        )
        fact_id = r.json()["id"]
        # Simulate a crash mid-write: stuck at the 'pending' marker.
        await app.state.sqlite_store.set_fact_status(fact_id, "pending")

        sweep_task = asyncio.create_task(
            periodic_cleanup_sweep(
                app.state.sqlite_store,
                app.state.chroma_store,
                interval_seconds=0.05,
                older_than_seconds=0,
            )
        )
        try:
            await asyncio.sleep(0.2)
            assert await app.state.sqlite_store.get_fact(fact_id) is None
        finally:
            sweep_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweep_task


async def test_app_lifespan_launches_and_cancels_periodic_sweep(tmp_path):
    async with build_client(tmp_path, stale_pending_sweep_interval_seconds=0.05) as (client, app):
        sweep_task = app.state.stale_pending_sweep_task
        assert not sweep_task.done()
        await asyncio.sleep(0.15)

    assert sweep_task.cancelled()


async def test_app_lifespan_cancels_sweep_tasks_even_if_body_raises(tmp_path):
    # An exception escaping a route handler uncaught (e.g. a future bug, or
    # ToolCallWriteFailed before adapters/generic/adapter.py's caller caught
    # it) must not leak the background sweep tasks -- lifespan's cleanup has
    # to run regardless of how the `yield` exits, not just on the clean-exit
    # path already covered above.
    class Boom(Exception):
        pass

    sweep_task = None
    dedup_task = None
    with pytest.raises(Boom):
        async with build_client(tmp_path, stale_pending_sweep_interval_seconds=0.05) as (_client, app):
            sweep_task = app.state.stale_pending_sweep_task
            dedup_task = app.state.ingest_dedup_sweep_task
            raise Boom("simulated unhandled exception escaping a route handler")

    assert sweep_task.cancelled()
    assert dedup_task.cancelled()
