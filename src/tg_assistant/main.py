import asyncio
import logging

from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from tg_assistant.config import settings
from tg_assistant.bot.middlewares.current_user import CurrentUserMiddleware
from tg_assistant.bot.middlewares.db_session import DbSessionMiddleware
from tg_assistant.bot.middlewares.services import ServicesMiddleware

from tg_assistant.bot.routers.start import router as start_router
from tg_assistant.bot.routers.tasks import router as tasks_router
from tg_assistant.bot.routers.files import router as files_router
from tg_assistant.bot.routers.chat import router as chat_router
from tg_assistant.bot.routers.links import router as links_router

from tg_assistant.services.reminders import remind_overdue_tasks
from tg_assistant.services.ollama_service import OllamaService
from tg_assistant.services.chroma_service import ChromaService


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()

    # DB + user
    dp.update.middleware(DbSessionMiddleware())
    dp.update.middleware(CurrentUserMiddleware())

    # Services
    ollama = OllamaService()
    await ollama.start()

    try:
        chroma = ChromaService()
        chroma.client.heartbeat()
        logging.info("Chroma OK")
    except Exception:
        logging.exception("Chroma unavailable, continue without it for now")
        chroma = None

    dp.update.middleware(ServicesMiddleware(ollama=ollama, chroma=chroma))

    # Routers (подключаем ДО polling) [web:371]
    dp.include_router(start_router)
    dp.include_router(tasks_router)
    dp.include_router(links_router)

    dp.include_router(files_router)
    dp.include_router(chat_router)

    # Scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(remind_overdue_tasks, "interval", minutes=60, args=[bot])
    scheduler.start()

    try:
        # Запускаем polling один раз, после регистрации всего [web:93]
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await ollama.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
