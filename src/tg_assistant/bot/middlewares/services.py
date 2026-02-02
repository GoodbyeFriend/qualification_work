from typing import Any, Awaitable, Callable, Dict

from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import TelegramObject

from tg_assistant.services.ollama_service import OllamaService
from tg_assistant.services.chroma_service import ChromaService


class ServicesMiddleware(BaseMiddleware):
    def __init__(self, ollama: OllamaService, chroma: ChromaService | None):
        self.ollama = ollama
        self.chroma = chroma

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        data["ollama"] = self.ollama
        data["chroma"] = self.chroma  # может быть None
        return await handler(event, data)
