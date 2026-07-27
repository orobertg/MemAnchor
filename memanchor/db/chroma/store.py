from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import chromadb
from chromadb.utils import embedding_functions

COLLECTION_NAME = "memanchor_collection"


class ChromaStore:
    """Wraps ChromaDB's (synchronous) client behind async methods via
    asyncio.to_thread, since the embedded PersistentClient has no native
    async API and embedding computation is CPU-bound."""

    def __init__(self, path: str, embedding_model: str):
        self.path = path
        self.embedding_model = embedding_model
        self._client: chromadb.ClientAPI | None = None
        self._collection = None

    async def connect(self) -> None:
        Path(self.path).mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._connect_sync)

    def _connect_sync(self) -> None:
        self._client = chromadb.PersistentClient(path=self.path)
        embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=self.embedding_model
        )
        self._collection = self._client.get_or_create_collection(
            COLLECTION_NAME,
            embedding_function=embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )

    @property
    def collection(self):
        assert self._collection is not None, "ChromaStore.connect() must be called first"
        return self._collection

    async def upsert(self, id: str, content: str) -> None:
        await asyncio.to_thread(self.collection.upsert, ids=[id], documents=[content])

    async def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        await asyncio.to_thread(self.collection.delete, ids=ids)

    async def query(self, query_text: str, top_k: int) -> list[dict[str, Any]]:
        result = await asyncio.to_thread(
            self.collection.query,
            query_texts=[query_text],
            n_results=top_k,
            include=["distances", "embeddings"],
        )
        ids = result["ids"][0]
        distances = result["distances"][0]
        embeddings = result["embeddings"][0]
        return [
            {"id": id_, "distance": distance, "embedding": embedding}
            for id_, distance, embedding in zip(ids, distances, embeddings)
        ]

    async def heartbeat(self) -> bool:
        try:
            await asyncio.to_thread(self._client.heartbeat)
            return True
        except Exception:
            return False
