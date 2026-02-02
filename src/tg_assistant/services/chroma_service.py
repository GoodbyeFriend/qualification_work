from __future__ import annotations

from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings
import logging
logger = logging.getLogger(__name__)

from tg_assistant.config import settings


class ChromaService:
    def __init__(self):
        self.client = chromadb.HttpClient(
            host=settings.chroma_host,
            port=settings.chroma_port,
            settings=ChromaSettings(anonymized_telemetry=False),
        )

    def get_user_collection(self, user_id: int):
        return self.client.get_or_create_collection(
            name=f"user_{user_id}",
            metadata={"user_id": str(user_id)},
        )

    def upsert_embedding(
        self,
        user_id: int,
        doc_id: str,
        embedding: list[float],
        document: str,
        metadata: dict[str, Any],
    ) -> None:
        col = self.get_user_collection(user_id)
        col.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[document],
            metadatas=[metadata],
        )

    def query_by_embedding(
        self,
        user_id: int,
        query_embedding: list[float],
        n_results: int = 3,
        where: dict | None = None,
    ) -> list[dict[str, Any]]:
        col = self.get_user_collection(user_id)
        res = col.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        items: list[dict[str, Any]] = []
        for i in range(len(res["ids"][0])):
            items.append(
                {
                    "id": res["ids"][0][i],
                    "text": res["documents"][0][i],
                    "metadata": res["metadatas"][0][i],
                    "distance": res["distances"][0][i],
                }
            )
        logger.info("chroma.query user=%s n=%s", user_id, n_results)
        return items
    def delete_file_chunks(self, user_id: int, file_id: int) -> None:
        col = self.get_user_collection(user_id)
        col.delete(
            where={
                "$and": [
                    {"entity_type": "file"},
                    {"entity_id": file_id},
                    {"user_id": user_id},
                ]
            }
        )

