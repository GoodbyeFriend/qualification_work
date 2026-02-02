from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tg_assistant.config import settings

engine = create_async_engine(settings.database_url, echo=False)
SessionMaker = async_sessionmaker(engine, expire_on_commit=False)
