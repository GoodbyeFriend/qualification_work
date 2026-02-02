from __future__ import annotations
import logging

import hashlib
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import Message, FSInputFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
import re
from tg_assistant.config import settings
from tg_assistant.db.models.files import StoredFile
from tg_assistant.db.models.user import User
from tg_assistant.services.ollama_service import OllamaService
from tg_assistant.services.chroma_service import ChromaService
from tg_assistant.services.document_parser import (
    extract_text_from_pdf,
    extract_text_from_docx,
    chunk_text,
)

router = Router()

def sanitize_filename(name: str) -> str:
    """Убираем опасные символы из имени файла."""
    name = re.sub(r'[/\\?%*:|"<>]', '_', name)
    name = name[:200].rstrip()
    return name or "document"

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

logger = logging.getLogger(__name__)

@router.message(lambda m: m.document is not None)
async def on_document(
    message: Message,
    bot,
    session,
    current_user: User,
    ollama: OllamaService,
    chroma: ChromaService | None,
) -> None:
    doc = message.document
    if not doc:
        return

    allowed_mimes = {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
        "application/msword",  # .doc (на всякий)
    }
    if doc.mime_type not in allowed_mimes:
        await message.answer(
            f"❌ Поддерживаются только PDF и DOCX.\n"
            f"Ты отправил: {doc.mime_type or 'unknown'}"
        )
        return

    base_dir = Path(settings.data_dir) / "files" / str(current_user.id)
    base_dir.mkdir(parents=True, exist_ok=True)

    orig_name = sanitize_filename(doc.file_name or f"document_{doc.file_id}")
    tmp_path = base_dir / f"tmp_{doc.file_id}"

    # 1) download to tmp
    file = await bot.get_file(doc.file_id)
    await bot.download_file(file.file_path, destination=tmp_path)

    # 2) compute sha
    digest = sha256_file(tmp_path)

    # 3) final path based on sha
    final_path = base_dir / f"file_{digest[:8]}_{orig_name}"

    # 4) дедуп по tg_file_unique_id (если есть)
    tg_unique = doc.file_unique_id
    if tg_unique:
        stmt = select(StoredFile).where(
            StoredFile.user_id == current_user.id,
            StoredFile.tg_file_unique_id == tg_unique,
        )
        res = await session.execute(stmt)
        existing = res.scalar_one_or_none()
        if existing:
            tmp_path.unlink(missing_ok=True)
            await message.answer(f"Этот файл уже загружен как #{existing.id} — {existing.orig_name}")
            return

    # 5) дедуп по sha256
    stmt = select(StoredFile).where(
        StoredFile.user_id == current_user.id,
        StoredFile.sha256 == digest,
    )
    res = await session.execute(stmt)
    existing = res.scalar_one_or_none()
    if existing:
        tmp_path.unlink(missing_ok=True)
        await message.answer(f"Этот файл уже загружен как #{existing.id} — {existing.orig_name}")
        return

    stored = StoredFile(
        user_id=current_user.id,
        orig_name=orig_name,
        mime=doc.mime_type,
        size=doc.file_size,
        sha256=digest,
        tg_file_unique_id=tg_unique,
        local_path=str(final_path),
    )
    session.add(stored)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        tmp_path.unlink(missing_ok=True)
        await message.answer("Этот файл уже был загружен ранее (дубликат).")
        return

    await session.refresh(stored)

    # 6) переименовываем tmp -> final (и только тут)
    try:
        if final_path.exists():
            # на всякий случай: если уже есть файл с таким именем (например, гонки)
            tmp_path.unlink(missing_ok=True)
        else:
            tmp_path.rename(final_path)
    except Exception:
        logger.exception("Failed to move tmp file to final_path for file_id=%s", stored.id)
        await message.answer(f"✅ Файл сохранён: #{stored.id} ({orig_name})\n⚠️ Не удалось переместить файл в финальный путь.")
        return

    # 7) индексация
    if chroma is not None:
        try:
            path = Path(stored.local_path)
            if stored.mime == "application/pdf":
                text = extract_text_from_pdf(path)
            else:
                text = extract_text_from_docx(path)

            chunks = chunk_text(text)
            if not chunks:
                await message.answer(
                    f"✅ Файл сохранён: #{stored.id} ({orig_name})\n"
                    f"⚠️ Не удалось извлечь текст для индексации."
                )
                return

            embeddings = await ollama.embed(chunks)

            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                chroma.upsert_embedding(
                    user_id=current_user.id,
                    doc_id=f"file_{stored.id}_chunk_{i}",
                    embedding=emb,
                    document=chunk,
                    metadata={
                        "entity_type": "file",
                        "entity_id": stored.id,
                        "chunk": i,
                        "filename": stored.orig_name,
                        "mime": stored.mime,
                        "user_id": current_user.id,
                    },
                )

            await message.answer(f"✅ Файл сохранён и проиндексирован: #{stored.id} ({orig_name})")
        except Exception:
            logger.exception("Indexing failed for file_id=%s", stored.id)
            await message.answer(f"✅ Файл сохранён: #{stored.id} ({orig_name})\n⚠️ Индексация не удалась (см. логи).")
            return
    else:
        await message.answer(f"✅ Файл сохранён: #{stored.id} ({orig_name})")

@router.message(Command("files"))
async def list_files(message: Message, session, current_user: User) -> None:
    stmt = (
        select(StoredFile)
        .where(StoredFile.user_id == current_user.id)
        .order_by(StoredFile.id.desc())
        .limit(20)
    )
    res = await session.execute(stmt)
    items = list(res.scalars().all())

    if not items:
        await message.answer("Файлов пока нет.")
        return

    lines = ["Последние файлы:"]
    for f in items:
        lines.append(f"#{f.id} — {f.orig_name}")
    await message.answer("\n".join(lines))


@router.message(Command("file"))
async def get_file_cmd(message: Message, command: CommandObject, session, current_user: User) -> None:
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Формат: /file ID\nПример: /file 3")
        return
    file_id = int(command.args.strip())

    stmt = select(StoredFile).where(StoredFile.id == file_id, StoredFile.user_id == current_user.id)
    res = await session.execute(stmt)
    f = res.scalar_one_or_none()
    if not f:
        await message.answer("Файл не найден.")
        return

    await message.answer_document(FSInputFile(f.local_path), caption=f.orig_name)
