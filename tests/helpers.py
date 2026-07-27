from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from httpx import ASGITransport

from memanchor.config import Config
from memanchor.endpoints.app import create_app


@asynccontextmanager
async def build_client(tmp_path, **config_overrides):
    config = Config(
        sqlite_path=str(tmp_path / "memanchor.db"),
        chroma_path=str(tmp_path / "chroma"),
        **config_overrides,
    )
    app = create_app(config)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, app
