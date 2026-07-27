"""FastAPI composition root.

Wires infrastructure, application services, and resource routers. Route
handlers live under ``endpoints/routers/``; use-cases under ``application/``.
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI

from memanchor.adapters.generic.adapter import GenericAdapter
from memanchor.application.write_pipeline import cleanup_stale_pending, periodic_cleanup_sweep
from memanchor.config import Config, load_config
from memanchor.contracts.adapter import load_fact_shape
from memanchor.db.chroma.store import ChromaStore
from memanchor.db.sqlite.store import SqliteStore
from memanchor.endpoints.routers import health, memory, observer, registry, telemetry
from memanchor.observer.ingest import IngestDedupCache, periodic_dedup_sweep
from memanchor.telemetry.counters import TelemetryCounters


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        sqlite_store = SqliteStore(config.sqlite_path)
        chroma_store = ChromaStore(config.chroma_path, config.embedding_model)
        await sqlite_store.connect()
        await chroma_store.connect()
        await cleanup_stale_pending(sqlite_store, chroma_store)

        # Flat attributes on app.state (not nested AppState) so existing tests
        # and integrations that read app.state.sqlite_store / .adapter keep working.
        app.state.config = config
        app.state.sqlite_store = sqlite_store
        app.state.chroma_store = chroma_store
        app.state.telemetry = TelemetryCounters()
        app.state.adapter = GenericAdapter(
            sqlite_store, chroma_store, load_fact_shape(config.fact_shape_path), app.state.telemetry
        )
        app.state.ingest_dedup_cache = IngestDedupCache()

        # Startup sweep above only catches facts already stale at boot; this
        # background task catches ones that go stale mid-runtime, so a stuck
        # fact doesn't wait for the next restart to be recovered.
        app.state.stale_pending_sweep_task = asyncio.create_task(
            periodic_cleanup_sweep(
                sqlite_store, chroma_store, interval_seconds=config.stale_pending_sweep_interval_seconds
            )
        )

        # Same rationale as the stale-pending sweep above: get()'s lazy purge
        # alone leaves a write-once, never-requeried dedup key in memory for
        # the life of the process.
        app.state.ingest_dedup_sweep_task = asyncio.create_task(
            periodic_dedup_sweep(
                app.state.ingest_dedup_cache, interval_seconds=config.ingest_dedup_sweep_interval_seconds
            )
        )

        try:
            yield
        finally:
            # Must run even if the yield exits via an exception (e.g. one
            # that escaped a route handler uncaught) -- otherwise the
            # periodic sweep tasks leak and the SQLite connection is never
            # closed.
            for task in (app.state.stale_pending_sweep_task, app.state.ingest_dedup_sweep_task):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

            await sqlite_store.close()

    app = FastAPI(title="MemAnchor", lifespan=lifespan)
    app.include_router(memory.router)
    app.include_router(registry.router)
    app.include_router(observer.router)
    app.include_router(telemetry.router)
    app.include_router(health.router)
    return app


app = create_app()
