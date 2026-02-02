from __future__ import annotations

import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import Message
from pathlib import Path
from tg_assistant.config import settings

from tg_assistant.db.models.user import User
from tg_assistant.services.ollama_service import OllamaService
from tg_assistant.services.chroma_service import ChromaService
from tg_assistant.services.link_fetcher import extract_urls, fetch_url_text,html_to_text,fetch_url_html
from tg_assistant.services.links import create_link, list_links, get_link

logger = logging.getLogger(__name__)
router = Router()

@router.message(F.text.regexp(r"https?://"))
async def on_link_message(message: Message, session, current_user: User, ollama: OllamaService, chroma: ChromaService | None):
    text = message.text or ""
    urls = extract_urls(text)
    if not urls:
        return

    urls = urls[:2]

    for url in urls:
        status = await message.answer(f"Сохраняю ссылку: {url}")

        try:
            html = await fetch_url_html(url, timeout_s=25)
            title, page_text = html_to_text(html)  # БЕЗ await
        except Exception:
            logger.exception("fetch failed url=%s", url)
            await status.edit_text(f"Не смог скачать страницу: {url}")
            continue

        # сохраняем в SQL
        link = await create_link(session, current_user.id, url, title, page_text)
        # DEBUG: сохраняем на диск сырой html и текст
        base_dir = Path(settings.data_dir) / "links" / str(current_user.id)
        base_dir.mkdir(parents=True, exist_ok=True)

        

        html_path = base_dir / f"link_{link.id}.html"
        txt_path = base_dir / f"link_{link.id}.txt"

        html_path.write_text(html, encoding="utf-8", errors="ignore")
        txt_path.write_text(page_text, encoding="utf-8", errors="ignore")
        # индексируем в Chroma (одним большим документом или чанками)
        if chroma is not None:
            try:
                doc = f"{title}\nURL: {url}\n\n{page_text}"
                emb = (await ollama.embed([doc]))[0]
                chroma.upsert_embedding(
                    user_id=current_user.id,
                    doc_id=f"link_{link.id}",
                    embedding=emb,
                    document=doc,
                    metadata={
                        "entity_type": "link",
                        "entity_id": link.id,
                        "url": url,
                        "title": title,
                        "user_id": current_user.id,
                    },
                )
            except Exception:
                logger.exception("index link failed id=%s url=%s", link.id, url)

        await status.edit_text(f"✅ Ссылка сохранена как #{link.id}\n{title or url}")

@router.message(Command("links"))
async def links_cmd(message: Message, session, current_user: User):
    items = await list_links(session, current_user.id, limit=20)
    if not items:
        await message.answer("Ссылок пока нет.")
        return
    lines = ["Последние ссылки:"]
    for l in items:
        t = (l.title or l.url)
        lines.append(f"#{l.id} — {t}")
    await message.answer("\n".join(lines))

@router.message(Command("link"))
async def link_cmd(message: Message, command: CommandObject, session, current_user: User):
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Формат: /link ID\nПример: /link 3")
        return
    link_id = int(command.args.strip())
    link = await get_link(session, current_user.id, link_id)
    if not link:
        await message.answer("Ссылка не найдена.")
        return
    await message.answer(f"{link.title or 'Ссылка'}\n{link.url}")
