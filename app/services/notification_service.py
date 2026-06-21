from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, time
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Exercise, Notification, User
from app.db.session import async_session_factory
from app.services.lesson_service import today_bounds_utc

logger = logging.getLogger(__name__)

REMINDER_TYPE = "daily_reminder"

REMINDER_GREETINGS = [
    "Занятие дня готово. 5 вопросов, пара минут - и ты ближе к нужному баллу.",
    "Время короткой тренировки. Не теряй серию - пройди занятие дня.",
    "5 вопросов по русскому уже ждут. После каждого - быстрый разбор.",
    "Пара минут на русский сегодня? Занятие уже собрано.",
]


def is_reminder_due(notification_time: time, tz_name: str, now_utc: datetime) -> bool:
    """Пора ли отправлять напоминание: совпадение часа и минуты в таймзоне пользователя."""
    try:
        tz = ZoneInfo(tz_name or get_settings().app_timezone)
    except Exception:  # noqa: BLE001
        tz = ZoneInfo(get_settings().app_timezone)
    now_local = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    return (now_local.hour, now_local.minute) == (notification_time.hour, notification_time.minute)


async def already_sent_today(session: AsyncSession, user: User) -> bool:
    start, end = today_bounds_utc(user.timezone)
    result = await session.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id,
            Notification.notification_type == REMINDER_TYPE,
            Notification.scheduled_at >= start,
            Notification.scheduled_at < end,
        )
    )
    return int(result.scalar() or 0) > 0


async def get_random_fact(session: AsyncSession) -> str | None:
    # "Правило дня" берем из реальных коротких разборов заданий, а не из
    # шаблонных "интересных фактов" (их вычистили как ИИ-наполнитель).
    result = await session.execute(
        select(Exercise.short_explanation)
        .where(Exercise.status == "published", Exercise.short_explanation.is_not(None))
        .order_by(func.random())
        .limit(1)
    )
    return result.scalar_one_or_none()


def build_reminder_text(fact: str | None) -> str:
    text = random.choice(REMINDER_GREETINGS)
    if fact:
        text += f"\n\n<i>Правило дня: {fact}</i>"
    return text


async def send_due_reminders(bot: Bot) -> int:
    """Отправляет напоминания, у которых наступило время. Возвращает число отправленных."""
    from app.bot.keyboards.main import reminder_keyboard

    now_utc = datetime.utcnow()
    sent = 0
    async with async_session_factory() as session:
        users_result = await session.execute(
            select(User).where(User.notifications_enabled.is_(True), User.status == "active")
        )
        users = list(users_result.scalars().all())

        for user in users:
            if not is_reminder_due(user.notification_time, user.timezone, now_utc):
                continue
            if await already_sent_today(session, user):
                continue

            fact = await get_random_fact(session)
            text = build_reminder_text(fact)
            try:
                await bot.send_message(user.telegram_id, text, reply_markup=reminder_keyboard())
            except TelegramForbiddenError:
                # Пользователь заблокировал бота - больше не беспокоим.
                user.notifications_enabled = False
                user.status = "blocked"
                await session.commit()
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("Не удалось отправить напоминание user_id=%s: %s", user.id, exc)
                continue

            session.add(
                Notification(
                    user_id=user.id,
                    notification_type=REMINDER_TYPE,
                    text=text,
                    status="sent",
                    scheduled_at=now_utc,
                    sent_at=now_utc,
                )
            )
            await session.commit()
            sent += 1
    return sent


ADMIN_REPORT_TYPE = "admin_daily_report"


def _parse_report_time(raw: str) -> time:
    try:
        hours, minutes = raw.strip().split(":")
        return time(int(hours), int(minutes))
    except (ValueError, AttributeError):
        return time(9, 0)


async def build_admin_report(session: AsyncSession) -> str:
    """Сводка за вчера (в таймзоне приложения) для владельца."""
    from app.db.models import Lesson, Topic, UserAnswer

    from datetime import timedelta

    today_start, _ = today_bounds_utc()
    start = today_start - timedelta(days=1)  # вчера в таймзоне приложения
    end = today_start

    new_users = int((await session.execute(
        select(func.count(User.id)).where(User.created_at >= start, User.created_at < end)
    )).scalar() or 0)
    total_users = int((await session.execute(select(func.count(User.id)))).scalar() or 0)

    active_users = int((await session.execute(
        select(func.count(func.distinct(UserAnswer.user_id))).where(
            UserAnswer.answered_at >= start, UserAnswer.answered_at < end
        )
    )).scalar() or 0)

    answers = int((await session.execute(
        select(func.count(UserAnswer.id)).where(UserAnswer.answered_at >= start, UserAnswer.answered_at < end)
    )).scalar() or 0)
    correct = int((await session.execute(
        select(func.count(UserAnswer.id)).where(
            UserAnswer.answered_at >= start, UserAnswer.answered_at < end, UserAnswer.is_correct.is_(True)
        )
    )).scalar() or 0)
    accuracy = round((correct / answers) * 100, 1) if answers else 0

    lessons_done = int((await session.execute(
        select(func.count(Lesson.id)).where(
            Lesson.completed_at.is_not(None), Lesson.completed_at >= start, Lesson.completed_at < end
        )
    )).scalar() or 0)

    hardest_row = (await session.execute(
        select(Topic.title, func.count(UserAnswer.id).label("wrongs"))
        .join(Exercise, Exercise.topic_id == Topic.id)
        .join(UserAnswer, UserAnswer.exercise_id == Exercise.id)
        .where(UserAnswer.answered_at >= start, UserAnswer.answered_at < end, UserAnswer.is_correct.is_(False))
        .group_by(Topic.title)
        .order_by(func.count(UserAnswer.id).desc())
        .limit(1)
    )).first()

    lines = [
        "<b>📈 Сводка за вчера</b>",
        "",
        f"Новых пользователей: <b>{new_users}</b> (всего: {total_users})",
        f"Активных: <b>{active_users}</b>",
        f"Ответов: <b>{answers}</b>, точность: <b>{accuracy}%</b>",
        f"Занятий завершено: <b>{lessons_done}</b>",
    ]
    if hardest_row:
        lines.append(f"Самая сложная тема: <b>{hardest_row[0]}</b> ({hardest_row[1]} ошибок)")
    if answers == 0:
        lines.extend(["", "Вчера было тихо. Возможно, пора позвать новых пользователей."])
    return "\n".join(lines)


async def send_admin_reports(bot: Bot) -> int:
    """Шлет сводку администраторам в заданное время. Возвращает число отправленных."""
    settings = get_settings()
    if not settings.enable_admin_report or not settings.admin_telegram_ids:
        return 0

    report_time = _parse_report_time(settings.admin_report_time)
    now_utc = datetime.utcnow()
    if not is_reminder_due(report_time, settings.app_timezone, now_utc):
        return 0

    sent = 0
    async with async_session_factory() as session:
        report: str | None = None
        for admin_tg_id in settings.admin_telegram_ids:
            admin_user = (await session.execute(
                select(User).where(User.telegram_id == admin_tg_id)
            )).scalar_one_or_none()
            if admin_user is None:
                # Админ еще ни разу не писал боту - Telegram не даст отправить.
                continue

            start, end = today_bounds_utc()
            already = int((await session.execute(
                select(func.count(Notification.id)).where(
                    Notification.user_id == admin_user.id,
                    Notification.notification_type == ADMIN_REPORT_TYPE,
                    Notification.scheduled_at >= start,
                    Notification.scheduled_at < end,
                )
            )).scalar() or 0)
            if already:
                continue

            if report is None:
                report = await build_admin_report(session)
            try:
                await bot.send_message(admin_tg_id, report)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Не удалось отправить сводку админу %s: %s", admin_tg_id, exc)
                continue
            session.add(
                Notification(
                    user_id=admin_user.id,
                    notification_type=ADMIN_REPORT_TYPE,
                    text=report,
                    status="sent",
                    scheduled_at=now_utc,
                    sent_at=now_utc,
                )
            )
            await session.commit()
            sent += 1
    return sent


async def reminder_loop(bot: Bot) -> None:
    """Фоновый цикл: раз в минуту проверяет напоминания и сводку владельцу."""
    logger.info("Планировщик напоминаний запущен")
    while True:
        try:
            await send_due_reminders(bot)
            await send_admin_reports(bot)
        except Exception:  # noqa: BLE001
            logger.exception("Ошибка в цикле напоминаний")
        await asyncio.sleep(60)
