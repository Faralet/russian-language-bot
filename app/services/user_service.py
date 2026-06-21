from __future__ import annotations

from datetime import datetime, time

from aiogram.types import User as TelegramUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import User


async def get_or_create_user(session: AsyncSession, tg_user: TelegramUser) -> User:
    settings = get_settings()
    result = await session.execute(select(User).where(User.telegram_id == tg_user.id))
    user = result.scalar_one_or_none()

    role = "admin" if tg_user.id in settings.admin_telegram_ids else "user"

    if user is None:
        user = User(
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
            language_code=tg_user.language_code,
            role=role,
            daily_question_limit=settings.free_daily_question_limit,
            last_active_at=datetime.utcnow(),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user

    user.username = tg_user.username
    user.first_name = tg_user.first_name
    user.last_name = tg_user.last_name
    user.language_code = tg_user.language_code
    user.role = role if role == "admin" else user.role
    user.last_active_at = datetime.utcnow()
    await session.commit()
    await session.refresh(user)
    return user


async def get_user_by_telegram_id(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()


async def set_goal(session: AsyncSession, user: User, goal: str) -> User:
    user.goal = goal
    user.last_active_at = datetime.utcnow()
    await session.commit()
    await session.refresh(user)
    return user


async def set_level(session: AsyncSession, user: User, level: str) -> User:
    user.level = level
    user.last_active_at = datetime.utcnow()
    await session.commit()
    await session.refresh(user)
    return user


async def set_notifications_enabled(session: AsyncSession, user: User, enabled: bool) -> User:
    user.notifications_enabled = enabled
    await session.commit()
    await session.refresh(user)
    return user


async def set_notification_time(session: AsyncSession, user: User, value: time) -> User:
    user.notification_time = value
    user.notifications_enabled = True
    await session.commit()
    await session.refresh(user)
    return user
