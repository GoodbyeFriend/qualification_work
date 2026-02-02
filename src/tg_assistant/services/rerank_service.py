from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from tg_assistant.config import settings
from tg_assistant.services.ollama_service import OllamaService


_INT_RE = re.compile(r"\d+")


@dataclass
class RerankItem:
    hit: dict[str, Any]
    rank: int


class RerankService:
    def __init__(self, ollama: OllamaService, model: str | None = None):
        self.ollama = ollama
        self.model = model or settings.ollama_rerank_model

    async def rerank_hits_oneshot(
        self,
        query: str,
        hits: list[dict[str, Any]],
        *,
        max_items: int = 8,
        max_doc_chars: int = 700,
        timeout_s: int = 240,
    ) -> list[dict[str, Any]]:
        """
        One-shot rerank: 1 запрос к модели, которая возвращает порядок кандидатов.
        Возвращает hits, отсортированные (лучший -> хуже).
        """
        hits = hits[:max_items]
        if len(hits) <= 1:
            return hits

        blocks: list[str] = []
        for idx, h in enumerate(hits, start=1):
            meta = h.get("metadata") or {}
            doc = (h.get("text") or "")[:max_doc_chars].replace("\n", " ").strip()
            title = meta.get("filename") or meta.get("entity_id") or meta.get("id") or "unknown"
            blocks.append(f"{idx}) title={title} | text={doc}")

        prompt = (
            "You are a reranking model.\n"
            "Task: rank candidates by relevance to the user query.\n"
            "Return ONLY candidate indices in best-to-worst order, separated by spaces.\n"
            "No extra text.\n\n"
            f"Query: {query}\n\n"
            "Candidates:\n" + "\n".join(blocks)
        )

        out = await self.ollama.chat(
            [{"role": "user", "content": prompt}],
            model=self.model,
            timeout_s=timeout_s,
        )

        nums = [int(x) for x in _INT_RE.findall((out or "").strip())]
        seen = set()
        order: list[int] = []
        for n in nums:
            if 1 <= n <= len(hits) and n not in seen:
                seen.add(n)
                order.append(n)

        if not order:
            return hits

        for i in range(1, len(hits) + 1):
            if i not in seen:
                order.append(i)

        return [hits[i - 1] for i in order]
