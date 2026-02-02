from typing import Any, Awaitable, Callable, Dict

from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from tg_assistant.services.users import get_or_create_user


class CurrentUserMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: Dict[str, Any]) -> Any:
        session: AsyncSession = data["session"]

        tg_user = data.get("event_from_user")
        if tg_user is not None:
            data["current_user"] = await get_or_create_user(session, tg_user.id)

        return await handler(event, data)
