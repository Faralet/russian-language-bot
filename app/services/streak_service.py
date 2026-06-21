from __future__ import annotations

"""Серии (streak), заморозка серии и дневная цель.

Лучшее из Duolingo: серия - главный рычаг удержания, а streak freeze снимает
тревогу «потерять прогресс». Логика обновления серии - чистая функция (легко
проверить), работа с БД - тонкая прослойка.
"""

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import User

# Рубежи серии, которые отмечаем празднованием (и дарим заморозку).
STREAK_MILESTONES = {7, 14, 30, 50, 100, 200, 365}


@dataclass
class StreakResult:
    streak: int
    freezes: int
    froze: bool          # серия сохранена заморозкой (был пропуск)
    reset: bool          # серия обнулилась
    counted_today: bool  # первая активность за сегодня (есть что показать)
    milestone: int | None


def today_local() -> date:
    return datetime.now(ZoneInfo(get_settings().app_timezone)).date()


def compute_streak(last_on: date | None, today: date, current_streak: int, freezes: int) -> StreakResult:
    """Чистый расчет новой серии по дате последней активности."""
    if last_on == today:
        return StreakResult(current_streak, freezes, False, False, False, None)
    if last_on is None:
        return StreakResult(1, freezes, False, False, True, None)

    gap = (today - last_on).days
    froze = False
    reset = False
    if gap <= 0:
        # Защита от «прошлого» last_on (часовые пояса): считаем как уже сегодня.
        return StreakResult(current_streak, freezes, False, False, False, None)
    if gap == 1:
        new_streak = current_streak + 1
    elif gap == 2 and freezes > 0:
        new_streak = current_streak + 1
        freezes -= 1
        froze = True
    else:
        new_streak = 1
        reset = True

    milestone = new_streak if new_streak in STREAK_MILESTONES else None
    return StreakResult(new_streak, freezes, froze, reset, True, milestone)


async def register_daily_activity(session: AsyncSession, user: User) -> StreakResult:
    """Обновляет серию пользователя по факту активности сегодня.

    Вызывать при завершении занятия. На крупном рубеже дарим одну заморозку.
    """
    today = today_local()
    res = compute_streak(
        user.last_activity_on,
        today,
        user.current_streak or 0,
        user.streak_freezes if user.streak_freezes is not None else 0,
    )
    if res.counted_today:
        user.current_streak = res.streak
        user.longest_streak = max(user.longest_streak or 0, res.streak)
        user.streak_freezes = res.freezes
        user.last_activity_on = today
        if res.milestone:
            user.streak_freezes = (user.streak_freezes or 0) + 1
        await session.commit()
        await session.refresh(user)
    return res


async def register_daily_activity_for(session: AsyncSession, user_id: int) -> tuple[StreakResult | None, int]:
    """register_daily_activity по user_id. Возвращает (результат, текущая серия)."""
    from sqlalchemy import select
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        return None, 0
    res = await register_daily_activity(session, user)
    return res, user.current_streak or 0


def progress_bar(done: int, total: int, width: int = 10) -> str:
    if total <= 0:
        return "░" * width
    filled = max(0, min(width, round(done / total * width)))
    return "▓" * filled + "░" * (width - filled)


def daily_goal_line(answered_today: int, goal: int) -> str:
    done = min(answered_today, goal)
    bar = progress_bar(done, goal)
    if answered_today >= goal:
        return f"🎯 Цель дня выполнена! {bar} {answered_today}/{goal}"
    return f"🎯 Цель дня: {bar} {done}/{goal}"


def streak_milestone_text(streak: int) -> str:
    return f"🎉 <b>{streak} дней подряд!</b> Это серьезный рубеж. Держим темп."


def streak_freeze_note(res: StreakResult) -> str | None:
    if res.froze:
        return "🧊 Пропуск закрыт заморозкой - серия сохранена."
    return None
