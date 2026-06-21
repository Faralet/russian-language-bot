from __future__ import annotations

"""Геймификация: достижения, уровни, цель дня.

Логика порогов - чистые функции над словарем статистики (их легко проверить).
Работа с БД (compute_stats, разблокировка) - тонкая прослойка сверху.
Тон - живой, без шаблонов и без выдуманных фактов.
"""

from dataclasses import dataclass
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Lesson, User, UserAchievement, UserAnswer


@dataclass(frozen=True)
class Achievement:
    code: str
    emoji: str
    title: str
    description: str
    check: Callable[[dict], bool]


# Достижения. check читает словарь stats (см. compute_stats).
ACHIEVEMENTS: list[Achievement] = [
    Achievement("first_lesson", "🎯", "Первый шаг", "Пройдено первое занятие",
                lambda s: s["lessons"] >= 1),
    Achievement("answers_50", "💪", "Полста", "50 ответов позади",
                lambda s: s["total"] >= 50),
    Achievement("answers_200", "🧠", "Двести", "200 ответов - серьезный объем",
                lambda s: s["total"] >= 200),
    Achievement("streak_3", "🔥", "Серия 3 дня", "Три дня подряд без пропусков",
                lambda s: s["streak"] >= 3),
    Achievement("streak_7", "🔥", "Неделя подряд", "Семь дней серии",
                lambda s: s["streak"] >= 7),
    Achievement("streak_30", "🏆", "Месяц дисциплины", "30 дней серии",
                lambda s: s["streak"] >= 30),
    Achievement("perfect", "✅", "Без единой ошибки", "Занятие на 100%",
                lambda s: s["has_perfect"]),
    Achievement("accuracy_80", "🎓", "Точность 80+", "80%+ точности при 30+ ответах",
                lambda s: s["total"] >= 30 and s["accuracy"] >= 80),
    Achievement("invite_1", "🤝", "Привел друга", "Один приглашенный друг",
                lambda s: s["referrals"] >= 1),
    Achievement("invite_5", "📣", "Амбассадор", "Пятеро приглашенных друзей",
                lambda s: s["referrals"] >= 5),
]

# Уровни: XP = число верных ответов. Пороги входа в уровень.
LEVEL_THRESHOLDS = [0, 20, 50, 100, 200, 350, 550, 800, 1150, 1600]
LEVEL_TITLES = [
    "Новичок", "Ученик", "Уверенный", "Знаток", "Грамотей",
    "Эксперт", "Мастер", "Виртуоз", "Профи", "Легенда",
]


def level_info(correct_answers: int) -> dict:
    """Уровень по числу верных ответов: номер, название, прогресс до следующего."""
    level_index = 0
    for i, threshold in enumerate(LEVEL_THRESHOLDS):
        if correct_answers >= threshold:
            level_index = i
    current_floor = LEVEL_THRESHOLDS[level_index]
    next_threshold = (
        LEVEL_THRESHOLDS[level_index + 1] if level_index + 1 < len(LEVEL_THRESHOLDS) else None
    )
    return {
        "level": level_index + 1,
        "title": LEVEL_TITLES[min(level_index, len(LEVEL_TITLES) - 1)],
        "xp": correct_answers,
        "current_floor": current_floor,
        "next_threshold": next_threshold,
        "to_next": (next_threshold - correct_answers) if next_threshold else 0,
    }


async def compute_stats(session: AsyncSession, user: User) -> dict:
    total = int((await session.execute(
        select(func.count(UserAnswer.id)).where(UserAnswer.user_id == user.id)
    )).scalar() or 0)
    correct = int((await session.execute(
        select(func.count(UserAnswer.id)).where(
            UserAnswer.user_id == user.id, UserAnswer.is_correct.is_(True)
        )
    )).scalar() or 0)
    lessons = int((await session.execute(
        select(func.count(Lesson.id)).where(
            Lesson.user_id == user.id, Lesson.status == "completed"
        )
    )).scalar() or 0)
    has_perfect = (await session.execute(
        select(Lesson.id).where(
            Lesson.user_id == user.id,
            Lesson.status == "completed",
            Lesson.wrong_answers == 0,
            Lesson.correct_answers > 0,
        ).limit(1)
    )).first() is not None
    accuracy = round((correct / total) * 100, 1) if total else 0.0
    referrals = user.referral_count or 0
    streak = user.current_streak or 0

    return {
        "total": total,
        "correct": correct,
        "lessons": lessons,
        "has_perfect": has_perfect,
        "accuracy": accuracy,
        "referrals": referrals,
        "streak": streak,
    }


async def get_unlocked_codes(session: AsyncSession, user_id: int) -> set[str]:
    rows = await session.execute(
        select(UserAchievement.code).where(UserAchievement.user_id == user_id)
    )
    return set(rows.scalars().all())


async def check_new_achievements(session: AsyncSession, user: User) -> list[Achievement]:
    """Возвращает только что разблокированные достижения (и сохраняет их)."""
    stats = await compute_stats(session, user)
    unlocked = await get_unlocked_codes(session, user.id)
    newly: list[Achievement] = []
    for ach in ACHIEVEMENTS:
        if ach.code in unlocked:
            continue
        try:
            ok = bool(ach.check(stats))
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            session.add(UserAchievement(user_id=user.id, code=ach.code))
            newly.append(ach)
    if newly:
        await session.commit()
    return newly


async def check_new_achievements_for(session: AsyncSession, user_id: int) -> list[Achievement]:
    """То же, что check_new_achievements, но по user_id (грузит пользователя сам)."""
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        return []
    return await check_new_achievements(session, user)


async def get_earned_achievements(session: AsyncSession, user: User) -> list[Achievement]:
    unlocked = await get_unlocked_codes(session, user.id)
    return [a for a in ACHIEVEMENTS if a.code in unlocked]


def format_new_achievements(newly: list[Achievement]) -> str | None:
    if not newly:
        return None
    if len(newly) == 1:
        a = newly[0]
        return f"🏅 <b>Новое достижение:</b> {a.emoji} {a.title} - {a.description}."
    lines = ["🏅 <b>Новые достижения:</b>"]
    for a in newly:
        lines.append(f"{a.emoji} <b>{a.title}</b> - {a.description}")
    return "\n".join(lines)
