"""Transactional rollback -- ChromaDB failure leaves no active SQLite
row."""

from helpers import build_client


async def test_chroma_failure_leaves_no_active_sqlite_row(tmp_path):
    async with build_client(tmp_path) as (client, app):
        async def failing_upsert(*args, **kwargs):
            raise RuntimeError("simulated chroma failure")

        app.state.chroma_store.upsert = failing_upsert

        r = await client.post(
            "/memory/store",
            json={
                "entity": "test",
                "attribute": "attr",
                "value": "v1",
                "content": "some content",
                "provenance": "system-derived",
                "source_channel": "observer_ingest",
            },
        )
        assert r.status_code == 500
        body = r.json()
        assert body["status"] == "error"
        assert body["error"]["code"] == "WRITE_ROLLBACK"

        cursor = await app.state.sqlite_store.conn.execute("SELECT * FROM facts")
        rows = await cursor.fetchall()
        assert len(rows) == 0
