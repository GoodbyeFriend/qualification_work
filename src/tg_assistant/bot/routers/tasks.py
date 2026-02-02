from __future__ import annotations

from datetime import datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import Message
from sqlalchemy import select, update

from tg_assistant.db.models.task import Task
from tg_assistant.db.models.user import User

router = Router()
DT_FORMAT = "%Y-%m-%d-%H:%M"


def parse_add_task_args(args: str | None) -> tuple[datetime, str]:
    if not args:
        raise ValueError("empty")

    # "/add_task YYYY-MM-DD-HH:MM текст..."
    parts = args.strip().split(maxsplit=1)
    if len(parts) < 2:
        raise ValueError("bad_format")

    due_raw, text = parts[0].strip(), parts[1].strip()
    due_at = datetime.strptime(due_raw, DT_FORMAT)
    if not text:
        raise ValueError("no_text")
    return due_at, text


@router.message(Command("add_task"))
async def add_task_handler(
    message: Message,
    command: CommandObject,
    session,
    current_user: User,
) -> None:
    try:
        due_at, text = parse_add_task_args(command.args)
    except ValueError:
        await message.answer(
            "Формат:\n"
            "/add_task YYYY-MM-DD-HH:MM текст задачи\n"
            "Пример:\n"
            "/add_task 2026-01-16-18:00 оплатить интернет"
        )
        return

    task = Task(user_id=current_user.id, text=text, due_at=due_at, status="open")
    session.add(task)
    await session.commit()
    await session.refresh(task)

    await message.answer(f"Задача #{task.id} создана, дедлайн: {due_at.strftime(DT_FORMAT)}")


@router.message(Command("tasks"))
async def list_tasks_handler(message: Message, session, current_user: User) -> None:
    stmt = (
        select(Task)
        .where(Task.user_id == current_user.id, Task.status == "open")
        .order_by(Task.due_at.is_(None), Task.due_at.asc(), Task.id.asc())
        .limit(50)
    )
    res = await session.execute(stmt)
    tasks = list(res.scalars().all())

    if not tasks:
        await message.answer("Открытых задач нет.")
        return

    lines = ["Открытые задачи:"]
    for t in tasks:
        due = t.due_at.strftime(DT_FORMAT) if t.due_at else "без дедлайна"
        lines.append(f"#{t.id} — {due} — {t.text}")

    await message.answer("\n".join(lines))


@router.message(Command("done"))
async def done_task_handler(message: Message, command: CommandObject, session, current_user: User) -> None:
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Формат: /done ID\nПример: /done 12")
        return

    task_id = int(command.args.strip())

    stmt = (
        update(Task)
        .where(Task.id == task_id, Task.user_id == current_user.id, Task.status == "open")
        .values(status="done", updated_at=datetime.utcnow())
    )
    res = await session.execute(stmt)
    await session.commit()

    if res.rowcount == 0:
        await message.answer("Не нашёл открытую задачу с таким ID.")
        return

    await message.answer(f"Ок, задача #{task_id} закрыта.")
