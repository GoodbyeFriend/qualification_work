from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import select, update

from tg_assistant.db.engine import SessionMaker
from tg_assistant.db.models.task import Task
from tg_assistant.db.models.user import User


async def remind_overdue_tasks(bot: Bot) -> None:
    now = datetime.utcnow()
    async with SessionMaker() as session:
        stmt = (
            select(Task, User.tg_user_id)
            .join(User, User.id == Task.user_id)
            .where(
                Task.status == "open",
                Task.due_at.is_not(None),
                Task.due_at <= now,
                (Task.last_reminded_at.is_(None)) |
                (Task.last_reminded_at <= 
                 (now - timedelta(minutes=Task.remind_every_minutes))),
            )
            .limit(50)
        )
        res = await session.execute(stmt)
        rows = res.all()

        for task, tg_user_id in rows:
            await bot.send_message(
                tg_user_id,
                f"Напоминание ({task.remind_every_minutes}): задача #{task.id} просрочена\n{task.text}",
            )
            await session.execute(
                update(Task).where(Task.id == task.id).values(last_reminded_at=now)
            )

        await session.commit()
