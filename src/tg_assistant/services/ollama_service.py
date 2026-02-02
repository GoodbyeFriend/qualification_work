from __future__ import annotations

from typing import Any

import aiohttp
from aiohttp import ClientTimeout

from tg_assistant.config import settings


class OllamaService:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            # Без дефолтного timeout — будем задавать per-request
            self._session = aiohttp.ClientSession()

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _post_json(self, path: str, payload: dict[str, Any], timeout_s: int) -> dict[str, Any]:
        if self._session is None or self._session.closed:
            await self.start()

        assert self._session is not None
        url = f"{self.base_url}{path}"
        timeout = ClientTimeout(total=timeout_s)

        async with self._session.post(url, json=payload, timeout=timeout) as r:
            r.raise_for_status()
            return await r.json()

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        timeout_s: int = 120,
    ) -> str:
        data = await self._post_json(
            "/api/chat",
            {
                "model": model or settings.ollama_chat_model,
                "messages": messages,
                "stream": False,
            },
            timeout_s=timeout_s,
        )
        return data["message"]["content"]

    async def embed(
        self,
        texts: list[str],
        model: str | None = None,
        timeout_s: int = 120,
    ) -> list[list[float]]:
        data = await self._post_json(
            "/api/embed",
            {"model": model or settings.ollama_embed_model, "input": texts},
            timeout_s=timeout_s,
        )
        return data["embeddings"]

    async def classify_intent(self, text: str, timeout_s: int = 60) -> dict[str, Any]:
        schema = {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "enum": ["file", "link", "qa"]},
                "query": {"type": "string"},
            },
            "required": ["intent", "query"],
        }

        data = await self._post_json(
            "/api/chat",
            {
                "model": settings.ollama_chat_model,
                "stream": False,
                "format": schema,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You classify user requests for a personal assistant.\n"
                            "Return JSON only.\n"
                            "intent:\n"
                            "- file: user wants you to send a stored document/file\n"
                            "- link: user wants you to send a previously saved URL\n"
                            "- qa: user wants an answer/explanation using context\n"
                            "query: rewrite the request as a short search query (Russian allowed)."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
            },
            timeout_s=timeout_s,
        )
        # Ollama вернет dict, где content уже будет JSON-объектом (как dict)
        return data["message"]["content"] if isinstance(data["message"]["content"], dict) else {}
