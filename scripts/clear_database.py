import asyncio
from pathlib import Path

from sqlalchemy import delete, select

from tg_assistant.db.engine import SessionMaker
from tg_assistant.db.models.files import StoredFile
from tg_assistant.db.models.link import Link
from tg_assistant.db.models.task import Task
from tg_assistant.db.models.user import User
from tg_assistant.services.chroma_service import ChromaService


def clear_chroma_collections() -> None:
    chroma = ChromaService()
    collections = chroma.client.list_collections()
    for collection in collections:
        chroma.client.delete_collection(name=collection.name)


def remove_local_files(paths: list[str]) -> None:
    for file_path in paths:
        if not file_path:
            continue
        path = Path(file_path)
        if path.exists():
            path.unlink()


async def clear_database() -> None:
    async with SessionMaker() as session:
        result = await session.execute(select(StoredFile.local_path))
        remove_local_files(list(result.scalars().all()))

        await session.execute(delete(Link))
        await session.execute(delete(Task))
        await session.execute(delete(StoredFile))
        await session.execute(delete(User))
        await session.commit()


def main() -> None:
    clear_chroma_collections()
    asyncio.run(clear_database())


if __name__ == "__main__":
    main()
