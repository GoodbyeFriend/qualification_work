import asyncio
import logging
from pathlib import Path

from sqlalchemy import select

from tg_assistant.db.engine import SessionMaker
from tg_assistant.db.models.files import StoredFile
from tg_assistant.services.ollama_service import OllamaService
from tg_assistant.services.chroma_service import ChromaService
from tg_assistant.services.document_parser import (
    extract_text_from_pdf,
    extract_text_from_docx,
    chunk_text,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reindex")


async def main() -> None:
    ollama = OllamaService()
    chroma = ChromaService()

    async with SessionMaker() as session:
        res = await session.execute(select(StoredFile).order_by(StoredFile.id.asc()))
        files = list(res.scalars().all())

    logger.info("Found %s files to reindex", len(files))

    for f in files:
        path = Path(f.local_path)
        if not path.exists():
            logger.warning("Skip file_id=%s, path missing: %s", f.id, f.local_path)
            continue

        # 1) удалить старые чанки (если были)
        try:
            chroma.delete_file_chunks(user_id=f.user_id, file_id=f.id)
        except Exception:
            logger.exception("Failed delete old chunks for file_id=%s", f.id)

        # 2) извлечь текст
        try:
            if f.mime == "application/pdf":
                text = extract_text_from_pdf(path)
            else:
                text = extract_text_from_docx(path)
        except Exception:
            logger.exception("Failed extract text for file_id=%s", f.id)
            continue

        chunks = chunk_text(text)
        if not chunks:
            logger.warning("No text extracted for file_id=%s (%s)", f.id, f.orig_name)
            continue

        # 3) embeddings батчем
        try:
            embs = await ollama.embed(chunks)
        except Exception:
            logger.exception("Failed embeddings for file_id=%s", f.id)
            continue

        # 4) upsert
        for i, (chunk, emb) in enumerate(zip(chunks, embs)):
            chroma.upsert_embedding(
                user_id=f.user_id,
                doc_id=f"file_{f.id}_chunk_{i}",
                embedding=emb,
                document=chunk,
                metadata={
                    "user_id": f.user_id,
                    "entity_type": "file",
                    "entity_id": f.id,
                    "chunk": i,
                    "filename": f.orig_name,
                    "mime": f.mime,
                },
            )

        logger.info("Reindexed file_id=%s chunks=%s name=%s", f.id, len(chunks), f.orig_name)


if __name__ == "__main__":
    asyncio.run(main())
