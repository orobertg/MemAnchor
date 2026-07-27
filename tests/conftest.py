import pytest_asyncio

from helpers import build_client


@pytest_asyncio.fixture
async def client(tmp_path):
    async with build_client(tmp_path) as (client, _app):
        yield client
