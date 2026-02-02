import asyncio
from pathlib import Path
from sqlalchemy import select, delete

from tg_assistant.db.engine import SessionMaker
from tg_assistant.db.models.files import StoredFile
from tg_assistant.services.chroma_service import ChromaService


USER_ID = 1
FILE_ID = 3


async def main():
    chroma = ChromaService()
    chroma.delete_file_chunks(user_id=USER_ID, file_id=FILE_ID)

    async with SessionMaker() as session:
        res = await session.execute(
            select(StoredFile).where(
                StoredFile.id == FILE_ID,
                StoredFile.user_id == USER_ID,
            )
        )
        stored = res.scalar_one_or_none()
        if stored and stored.local_path:
            p = Path(stored.local_path)
            if p.exists():
                p.unlink()

        await session.execute(
            delete(StoredFile).where(
                StoredFile.id == FILE_ID,
                StoredFile.user_id == USER_ID,
            )
        )
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
    