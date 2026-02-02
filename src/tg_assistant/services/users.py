from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_assistant.db.models.user import User


async def get_or_create_user(session: AsyncSession, tg_user_id: int) -> User:
    res = await session.execute(select(User).where(User.tg_user_id == tg_user_id))
    user = res.scalar_one_or_none()
    if user:
        return user

    user = User(tg_user_id=tg_user_id)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user
