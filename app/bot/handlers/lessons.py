from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message, User as TelegramUser
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.main import (
    after_answer_keyboard,
    exercise_options_keyboard,
    main_menu_keyboard,
    premium_keyboard,
    topics_keyboard,
)
from app.config import get_settings
from app.db.models import User
from app.services.lesson_service import (
    answer_exercise,
    cancel_lesson,
    can_start_lesson,
    create_lesson,
    create_mistakes_lesson,
    get_active_topics,
    get_exercise_details,
    get_lesson_summary,
    get_next_exercise,
    get_progress_text,
    get_topic,
    save_rule,
)
from app.services.user_service import get_or_create_user

router = Router()


def is_active_premium(user: User) -> bool:
    return bool(user.is_premium and user.premium_until and user.premium_until > datetime.utcnow())


async def safe_answer(target: Any, text: str, **kwargs: Any) -> None:
    """Отправляет сообщение в обычный Message из Telegram.

    В callback-сценариях callback.message принадлежит боту, поэтому пользователя
    всегда берем из callback.from_user, а сообщение используем только как канал ответа.
    """
    await target.answer(text, **kwargs)


async def send_next_question(
    target_message: Message,
    session: AsyncSession,
    user_id: int,
    lesson_id: int,
) -> None:
    result = await get_next_exercise(session, lesson_id, user_id)
    if result is None:
        lesson = await get_lesson_summary(session, lesson_id, user_id)
        if lesson is None:
            await target_message.answer("Занятие не найдено. Попробуйте начать заново.")
            return
        total = lesson.correct_answers + lesson.wrong_answers
        percent = round((lesson.correct_answers / total) * 100, 1) if total else 0
        text = (
            "<b>Готово.</b>\n\n"
            "Вы прошли занятие.\n"
            f"Правильных ответов: <b>{lesson.correct_answers}</b> из <b>{total}</b>.\n"
            f"Точность: <b>{percent}%</b>.\n\n"
            "Хорошая работа. Маленькая языковая победа тоже победа."
        )
        await target_message.answer(text, reply_markup=main_menu_keyboard())
        return

    lesson, exercise = result
    answered = lesson.correct_answers + lesson.wrong_answers
    number = answered + 1
    text = f"<b>Вопрос {number} из {lesson.total_questions}</b>\n\n{escape(exercise.question)}"
    await target_message.answer(text, reply_markup=exercise_options_keyboard(exercise, lesson.id))


async def start_lesson_for_user(
    target_message: Message,
    session: AsyncSession,
    telegram_user: TelegramUser,
    lesson_type: str = "daily",
    topic_id: int | None = None,
) -> None:
    user = await get_or_create_user(session, telegram_user)

    allowed, used, limit = await can_start_lesson(session, user)
    if not allowed:
        text = (
            "<b>На сегодня бесплатная разминка закончилась.</b>\n\n"
            f"Вы уже прошли <b>{used}</b> из <b>{limit}</b> вопросов. Это хороший темп.\n\n"
            "В Premium можно заниматься без лимита, повторять ошибки и открывать сложные темы."
        )
        await target_message.answer(text, reply_markup=premium_keyboard())
        return

    # Чтобы бесплатный пользователь не мог превысить дневной лимит, если уже
    # прошел часть вопросов сегодня, занятие укорачивается до остатка лимита.
    settings = get_settings()
    questions_count = settings.lesson_questions_count
    if not is_active_premium(user):
        remaining = max(limit - used, 0)
        questions_count = max(1, min(settings.lesson_questions_count, remaining))

    if lesson_type == "mistakes":
        lesson = await create_mistakes_lesson(session, user, questions_count=questions_count)
    else:
        lesson = await create_lesson(
            session,
            user,
            lesson_type=lesson_type,
            topic_id=topic_id,
            questions_count=questions_count,
        )

    if lesson is None:
        await target_message.answer("Пока нет упражнений для этой темы. Добавим контент - и она оживет.")
        return

    await target_message.answer("Начинаем. Коротко, точно, без школьной пыли.")
    await send_next_question(target_message, session, user.id, lesson.id)


@router.message(F.text == "📚 Занятие дня")
async def daily_lesson_message(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    await start_lesson_for_user(message, session, message.from_user, lesson_type="daily")


@router.callback_query(F.data == "lesson:daily")
async def daily_lesson_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None:
        await callback.answer("Не удалось открыть занятие. Напишите /start.", show_alert=True)
        return
    await callback.answer()
    await start_lesson_for_user(callback.message, session, callback.from_user, lesson_type="daily")


@router.message(F.text == "🎯 Выбрать тему")
async def topic_list_message(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    user = await get_or_create_user(session, message.from_user)
    topics = await get_active_topics(session, include_premium=is_active_premium(user))
    await message.answer("Выберите тему:", reply_markup=topics_keyboard(topics))


@router.callback_query(F.data == "topics:list")
async def topic_list_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await get_or_create_user(session, callback.from_user)
    topics = await get_active_topics(session, include_premium=is_active_premium(user))
    await callback.message.answer("Выберите тему:", reply_markup=topics_keyboard(topics))
    await callback.answer()


@router.callback_query(F.data.startswith("topic:"))
async def topic_start_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None:
        await callback.answer("Не удалось открыть тему. Напишите /start.", show_alert=True)
        return
    user = await get_or_create_user(session, callback.from_user)
    try:
        topic_id = int(callback.data.split(":", 1)[1])
    except (AttributeError, ValueError, IndexError):
        await callback.message.answer("Тема не найдена.")
        await callback.answer()
        return

    topic = await get_topic(session, topic_id)
    if topic is None:
        await callback.message.answer("Тема не найдена.")
        await callback.answer()
        return
    if topic.is_premium and not is_active_premium(user):
        await callback.message.answer(
            "Эта тема входит в Premium. Бесплатно можно тренироваться в базовых разделах.",
            reply_markup=premium_keyboard(),
        )
        await callback.answer()
        return
    await callback.answer()
    await start_lesson_for_user(callback.message, session, callback.from_user, lesson_type="topic", topic_id=topic.id)


@router.message(F.text == "🧩 Мои ошибки")
async def mistakes_lesson_message(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    await start_lesson_for_user(message, session, message.from_user, lesson_type="mistakes")


@router.callback_query(F.data.startswith("ans:"))
async def answer_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await get_or_create_user(session, callback.from_user)
    try:
        _, lesson_id_raw, exercise_id_raw, option_id_raw = callback.data.split(":")
        lesson_id = int(lesson_id_raw)
        exercise_id = int(exercise_id_raw)
        option_id = int(option_id_raw)
    except (AttributeError, ValueError):
        await callback.message.answer("Не получилось обработать ответ. Попробуйте начать занятие заново.")
        await callback.answer()
        return

    result = await answer_exercise(session, user, lesson_id, exercise_id, option_id)
    if result is None:
        await callback.message.answer("Не получилось обработать ответ. Попробуйте начать занятие заново.")
        await callback.answer()
        return

    verdict = "Верно." if result.is_correct else "Почти. Правильный ответ: " + escape(result.correct_option_text)
    lines = [f"<b>{verdict}</b>", "", escape(result.short_explanation)]
    if result.example_text:
        lines.extend(["", f"<b>Пример:</b> {escape(result.example_text)}"])
    if result.interesting_fact:
        lines.extend(["", f"<i>{escape(result.interesting_fact)}</i>"])

    await callback.message.answer("\n".join(lines), reply_markup=after_answer_keyboard(lesson_id, exercise_id))
    await callback.answer()


@router.callback_query(F.data.startswith("next:"))
async def next_question_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await get_or_create_user(session, callback.from_user)
    try:
        lesson_id = int(callback.data.split(":", 1)[1])
    except (AttributeError, ValueError, IndexError):
        await callback.message.answer("Занятие не найдено. Попробуйте начать заново.")
        await callback.answer()
        return
    await send_next_question(callback.message, session, user.id, lesson_id)
    await callback.answer()


@router.callback_query(F.data.startswith("finish:"))
async def finish_lesson_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await get_or_create_user(session, callback.from_user)
    try:
        lesson_id = int(callback.data.split(":", 1)[1])
    except (AttributeError, ValueError, IndexError):
        await callback.message.answer("Занятие не найдено.", reply_markup=main_menu_keyboard())
        await callback.answer()
        return
    lesson = await cancel_lesson(session, lesson_id, user.id)
    if lesson is None:
        await callback.message.answer("Занятие не найдено.", reply_markup=main_menu_keyboard())
    else:
        await callback.message.answer(
            "Занятие остановлено. Можно вернуться позже или начать новую короткую тренировку.",
            reply_markup=main_menu_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("full:"))
async def full_explanation_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    try:
        exercise_id = int(callback.data.split(":", 1)[1])
    except (AttributeError, ValueError, IndexError):
        await callback.message.answer("Не удалось открыть подробное объяснение.")
        await callback.answer()
        return
    exercise = await get_exercise_details(session, exercise_id)
    if exercise is None:
        await callback.message.answer("Упражнение не найдено.")
    else:
        lines = ["<b>Подробное объяснение</b>", "", escape(exercise.full_explanation or exercise.short_explanation)]
        if exercise.example_text:
            lines.extend(["", f"<b>Пример:</b> {escape(exercise.example_text)}"])
        await callback.message.answer("\n".join(lines))
    await callback.answer()


@router.callback_query(F.data.startswith("save_rule:"))
async def save_rule_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await get_or_create_user(session, callback.from_user)
    try:
        exercise_id = int(callback.data.split(":", 1)[1])
    except (AttributeError, ValueError, IndexError):
        await callback.message.answer("Не удалось сохранить правило.")
        await callback.answer()
        return
    saved = await save_rule(session, user, exercise_id)
    if saved is None:
        await callback.message.answer("Не удалось сохранить правило.")
    else:
        await callback.message.answer("Правило сохранено. Маленькая личная библиотека грамотности пополнилась.")
    await callback.answer()


@router.message(F.text == "🏆 Мой прогресс")
async def progress_message(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    user = await get_or_create_user(session, message.from_user)
    text = await get_progress_text(session, user)
    await message.answer(text)


@router.callback_query(F.data == "progress:show")
async def progress_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await get_or_create_user(session, callback.from_user)
    text = await get_progress_text(session, user)
    await callback.message.answer(text)
    await callback.answer()
