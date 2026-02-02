from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_assistant.db.models.link import Link


async def create_link(session: AsyncSession, user_id: int, url: str, title: str, content: str) -> Link:
    link = Link(user_id=user_id, url=url, title=title or None, content_summary=content or None)
    session.add(link)
    await session.commit()
    await session.refresh(link)
    return link


async def list_links(session: AsyncSession, user_id: int, limit: int = 20) -> list[Link]:
    res = await session.execute(
        select(Link).where(Link.user_id == user_id).order_by(Link.id.desc()).limit(limit)
    )
    return list(res.scalars().all())


async def get_link(session: AsyncSession, user_id: int, link_id: int) -> Link | None:
    res = await session.execute(select(Link).where(Link.user_id == user_id, Link.id == link_id))
    return res.scalar_one_or_none()
