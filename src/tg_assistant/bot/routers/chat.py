from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from aiogram import Router, F
from aiogram.types import Message, FSInputFile
from sqlalchemy import select

from tg_assistant.services.rerank_service import RerankService
from tg_assistant.db.models.files import StoredFile
from tg_assistant.db.models.user import User
from tg_assistant.db.models.link import Link
from tg_assistant.config import settings
from tg_assistant.services.ollama_service import OllamaService
from tg_assistant.services.chroma_service import ChromaService
from tg_assistant.services.speech_to_text import SpeechToTextService

logger = logging.getLogger(__name__)
router = Router()

FILE_DISTANCE_THRESHOLD = 0.90
LINK_DISTANCE_THRESHOLD = 0.90
AMBIGUOUS_DELTA = 0.05

def pick_best_links(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    link_hits = [h for h in hits if (h.get("metadata") or {}).get("entity_type") == "link"]
    best_by_link: dict[int, dict[str, Any]] = {}
    for h in link_hits:
        meta = h.get("metadata") or {}
        try:
            link_id = int(meta["entity_id"])
        except Exception:
            continue
        if link_id not in best_by_link or h["distance"] < best_by_link[link_id]["distance"]:
            best_by_link[link_id] = h
    return sorted(best_by_link.values(), key=lambda x: x["distance"])

def build_context(items: list[dict[str, Any]], max_chars: int = 1200) -> str:
    parts: list[str] = []
    total = 0
    for it in items:
        meta = it.get("metadata") or {}
        src = f'{meta.get("entity_type", "unknown")}:{meta.get("entity_id", "?")} chunk={meta.get("chunk", "?")}'
        chunk = (it.get("text") or "").strip().replace("\n", " ")
        if not chunk:
            continue

        chunk = chunk[:500]
        block = f"[{src}]\n{chunk}\n"
        if total + len(block) > max_chars:
            break

        parts.append(block)
        total += len(block)

    return "\n".join(parts).strip()


def pick_best_files(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Из множества чанков выбираем по 1 лучшему чанку на файл (min distance),
    чтобы rerank делать по файлам (дешево), а не по десяткам чанков.
    """
    file_hits = [h for h in hits if (h.get("metadata") or {}).get("entity_type") == "file"]
    best_by_file: dict[int, dict[str, Any]] = {}

    for h in file_hits:
        meta = h.get("metadata") or {}
        try:
            file_id = int(meta["entity_id"])
        except Exception:
            continue

        if file_id not in best_by_file or h["distance"] < best_by_file[file_id]["distance"]:
            best_by_file[file_id] = h

    return sorted(best_by_file.values(), key=lambda x: x["distance"])

async def handle_text_query(
    message: Message,
    session,
    current_user: User,
    ollama: OllamaService,
    chroma: ChromaService | None,
    text: str,
    status_message: Message | None = None,
) -> None:
    text = text.strip()
    if not text:
        return
    status = status_message or await message.answer("Определяю тип запроса")

    intent_data = await ollama.classify_intent(text)
    intent = (intent_data or {}).get("intent", "qa")
    search_query = (intent_data or {}).get("query") or text

    status = await status.edit_text("Обрабатываю запрос, это может занять до 1–2 минут...")

    try:
        if chroma is None:
            await status.edit_text("Думаю...")
            reply = await ollama.chat([{"role": "user", "content": text}])
            await status.edit_text(reply)
            return

        reranker = RerankService(ollama)

        async def run_retrieval(where: dict | None) -> list[dict[str, Any]]:
            q_emb = (await ollama.embed([search_query]))[0]
            return chroma.query_by_embedding(current_user.id, q_emb, n_results=60, where=where)

        # 1) intent-based retrieval
        where: dict | None = None
        if intent == "file":
            where = {"entity_type": "file"}
        elif intent == "link":
            where = {"entity_type": "link"}

        raw_hits = await run_retrieval(where)

        # Если intent=file/link и ничего не нашли — пробуем fallback на общий поиск (qa)
        if not raw_hits and intent in {"file", "link"}:
            raw_hits = await run_retrieval(where=None)
            intent = "qa"

        # Если вообще ничего не нашли
        if not raw_hits:
            await status.edit_text("Ничего не нашлось, отвечаю без контекста...")
            reply = await ollama.chat([{"role": "user", "content": text}])
            await status.edit_text(reply)
            return

        # 2) FILE branch
        if intent == "file":
            candidates = pick_best_files(raw_hits)
            if not candidates:
                await status.edit_text("Похожих документов не нашлось. Попробуй уточнить или посмотри /files")
                return

            if len(candidates) >= 2:
                d0 = float(candidates[0].get("distance") or 999.0)
                d1 = float(candidates[1].get("distance") or 999.0)
                if d0 <= FILE_DISTANCE_THRESHOLD and (d1 - d0) >= AMBIGUOUS_DELTA:
                    reranked_files = candidates[:1]
                else:
                    await status.edit_text("Нашёл кандидатов, уточняю релевантность (rerank)...")
                    reranked_files = await reranker.rerank_hits_oneshot(
                        search_query,
                        candidates[:8],
                        max_items=min(8, len(candidates)),
                        timeout_s=240,
                    )
            else:
                reranked_files = candidates[:1]

            best = reranked_files[0]
            best_meta = best["metadata"]
            best_file_id = int(best_meta["entity_id"])
            best_distance = float(best.get("distance") or 999.0)

            if best_distance > FILE_DISTANCE_THRESHOLD:
                lines = ["Нашёл что-то похожее, но не уверен достаточно. Выбери файл вручную:"]
                for c in candidates[:3]:
                    m = c["metadata"]
                    lines.append(
                        f"#{m['entity_id']} — {m.get('filename', 'без имени')} "
                        f"(dist={float(c.get('distance') or 0.0):.3f})"
                    )
                lines.append("Можно отправить так: /file ID")
                await status.edit_text("\n".join(lines))
                return

            stmt = select(StoredFile).where(
                StoredFile.id == best_file_id,
                StoredFile.user_id == current_user.id,
            )
            res = await session.execute(stmt)
            stored = res.scalar_one_or_none()
            if stored is None:
                await status.edit_text("Нашёл индекс файла, но записи файла в БД нет.")
                return

            await status.edit_text("Готово, отправляю файл...")
            await message.answer_document(
                FSInputFile(stored.local_path, filename=stored.orig_name),
                caption=stored.orig_name,
            )
            await status.delete()
            return

        # 3) LINK branch
        if intent == "link":
            candidates = pick_best_links(raw_hits)
            if not candidates:
                await status.edit_text("Похожих ссылок не нашлось. Попробуй уточнить или посмотри /links")
                return

            if len(candidates) >= 2:
                d0 = float(candidates[0].get("distance") or 999.0)
                d1 = float(candidates[1].get("distance") or 999.0)
                if d0 <= LINK_DISTANCE_THRESHOLD and (d1 - d0) >= AMBIGUOUS_DELTA:
                    reranked_links = candidates[:1]
                else:
                    await status.edit_text("Нашёл кандидатов, уточняю релевантность (rerank)...")
                    reranked_links = await reranker.rerank_hits_oneshot(
                        search_query,
                        candidates[:8],
                        max_items=min(8, len(candidates)),
                        timeout_s=240,
                    )
            else:
                reranked_links = candidates[:1]

            best = reranked_links[0]
            link_id = int(best["metadata"]["entity_id"])
            best_distance = float(best.get("distance") or 999.0)

            if best_distance > LINK_DISTANCE_THRESHOLD:
                lines = ["Нашёл несколько похожих ссылок, но не уверен. Выбери вручную:"]
                for c in candidates[:3]:
                    m = c["metadata"]
                    title = m.get("title") or m.get("url") or "без названия"
                    lines.append(
                        f"#{m['entity_id']} — {title} "
                        f"(dist={float(c.get('distance') or 0.0):.3f})"
                    )
                lines.append("Можно отправить так: /link ID")
                await status.edit_text("\n".join(lines))
                return

            stmt = select(Link).where(Link.id == link_id, Link.user_id == current_user.id)
            res = await session.execute(stmt)
            link = res.scalar_one_or_none()
            if not link:
                await status.edit_text("Нашёл ссылку в индексе, но записи в БД нет.")
                return

            await status.edit_text("Готово, отправляю ссылку...")
            await message.answer(f"{link.url}")
            await status.delete()
            return

        # 4) QA branch
        short_hits = raw_hits[:12]
        await status.edit_text("Подбираю контекст (rerank)...")
        reranked_hits = await reranker.rerank_hits_oneshot(
            search_query,
            short_hits,
            max_items=min(6, len(short_hits)),
            timeout_s=240,
        )

        context = build_context(reranked_hits)
        prompt = (
            "Ты личный ассистент.\n"
            "Ответь на вопрос пользователя, опираясь на КОНТЕКСТ ниже.\n"
            "Если в контексте нет ответа — скажи, что данных не найдено, и уточни, что нужно.\n\n"
            f"КОНТЕКСТ:\n{context}\n\n"
            f"ВОПРОС:\n{search_query}"
        )

        await status.edit_text("Формирую ответ...")
        reply = await ollama.chat([{"role": "user", "content": prompt}], timeout_s=240)
        await status.edit_text(reply)

    except Exception:
        logger.exception("chat_handler failed")
        try:
            await status.edit_text("Ошибка при обработке запроса. Посмотри логи бота.")
        except Exception:
            pass


async def convert_voice_to_wav(source_path: Path, target_path: Path) -> None:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-ar",
        "16000",
        "-ac",
        "1",
        str(target_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed with code {process.returncode}: {stderr.decode(errors='ignore') or stdout.decode(errors='ignore')}"
        )


@router.message(F.voice)
async def voice_handler(
    message: Message,
    bot,
    session,
    current_user: User,
    ollama: OllamaService,
    chroma: ChromaService | None,
    speech_to_text: SpeechToTextService | None,
) -> None:
    from tg_assistant.config import settings

    voice = message.voice
    if voice is None:
        return

    status = await message.answer("Распознаю голосовое сообщение...")

    base_dir = Path(settings.data_dir) / "voice" / str(current_user.id)
    base_dir.mkdir(parents=True, exist_ok=True)

    file_key = voice.file_unique_id or voice.file_id
    ogg_path = base_dir / f"voice_{file_key}.ogg"
    wav_path = base_dir / f"voice_{file_key}.wav"

    try:
        file = await bot.get_file(voice.file_id)
        await bot.download_file(file.file_path, destination=ogg_path)

        await convert_voice_to_wav(ogg_path, wav_path)
    except Exception:
        logger.exception("Failed to download or convert voice message")
        await status.edit_text("Не удалось обработать голосовое сообщение. Проверь ffmpeg и попробуй снова.")
        ogg_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)
        return

    if speech_to_text is None:
        await status.edit_text("Распознавание голоса не настроено.")
        ogg_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)
        return

    try:
        transcript = await speech_to_text.transcribe(wav_path)
    except Exception:
        logger.exception("Speech-to-text failed")
        await status.edit_text("Не удалось распознать голосовое сообщение.")
        return
    finally:
        ogg_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)

    if not transcript:
        await status.edit_text("Не удалось распознать текст из голосового сообщения.")
        return

    await status.edit_text(f"Распознал: {transcript}\nОбрабатываю запрос...")
    await handle_text_query(
        message=message,
        session=session,
        current_user=current_user,
        ollama=ollama,
        chroma=chroma,
        text=transcript,
        status_message=status,
    )


@router.message(F.text & ~F.text.startswith("/"))
async def chat_handler(
    message: Message,
    session,
    current_user: User,
    ollama: OllamaService,
    chroma: ChromaService | None,
) -> None:
    await handle_text_query(
        message=message,
        session=session,
        current_user=current_user,
        ollama=ollama,
        chroma=chroma,
        text=(message.text or ""),
    )
