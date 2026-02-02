from __future__ import annotations

import logging
from typing import Any

from aiogram import Router, F
from aiogram.types import Message, FSInputFile
from sqlalchemy import select

from tg_assistant.services.rerank_service import RerankService
from tg_assistant.db.models.files import StoredFile
from tg_assistant.db.models.user import User
from tg_assistant.services.ollama_service import OllamaService
from tg_assistant.services.chroma_service import ChromaService
from tg_assistant.db.models.link import Link


logger = logging.getLogger(__name__)
router = Router()

FILE_INTENT_KEYWORDS = {
    "файл", "документ", "pdf", "docx", "скинь", "пришли", "отправь", "верни", "дай",
}

FILE_DISTANCE_THRESHOLD = 0.90
AMBIGUOUS_DELTA = 0.05


def wants_file(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in FILE_INTENT_KEYWORDS)
from tg_assistant.db.models.link import Link

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

@router.message(F.text & ~F.text.startswith("/"))
async def chat_handler(
    message: Message,
    session,
    current_user: User,
    ollama: OllamaService,
    chroma: ChromaService | None,
) -> None:
    text = (message.text or "").strip()
    if not text:
        return
    status = await message.answer("Определяю тип запроса")

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

            best = candidates[0]
            link_id = int(best["metadata"]["entity_id"])

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
