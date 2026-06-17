from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, User as TelegramUser
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.main import (
    MAIN_MENU_LABELS,
    OPTION_LETTERS,
    after_answer_keyboard,
    diagnostic_finished_keyboard,
    exercise_options_keyboard,
    lesson_finished_keyboard,
    main_menu_keyboard,
    premium_keyboard,
    should_use_letters,
    topics_keyboard,
)
from app.config import get_settings
from app.services.gamification_service import check_new_achievements_for, format_new_achievements
from app.services.lesson_service import (
    EGE_TASK_BY_SLUG,
    answer_exercise,
    answer_text_exercise,
    cancel_lesson,
    can_start_lesson,
    count_user_answers_today,
    create_diagnostic_lesson,
    create_lesson,
    create_mistakes_lesson,
    get_active_topics,
    get_exercise_details,
    get_lesson_summary,
    get_lesson_topic_breakdown,
    get_next_exercise,
    get_notifications_enabled,
    get_overall_accuracy,
    get_path_overview,
    get_progress_text,
    get_saved_rules,
    get_streak_days,
    get_topic,
    is_active_premium,
    save_rule,
)
from app.services.referral_service import share_dialog_url
from app.services.score_service import score_line
from app.services.streak_service import (
    daily_goal_line,
    register_daily_activity_for,
    streak_freeze_note,
    streak_milestone_text,
)
from app.services.user_service import get_or_create_user

router = Router()


class LessonStates(StatesGroup):
    # Пользователь должен вписать ответ на текущий вопрос (формат ЕГЭ).
    typing = State()


def _progress_bar(current: int, total: int, width: int = 10) -> str:
    """Текстовый прогресс-бар: ▓▓▓░░░░░░░"""
    if total <= 0:
        return "░" * width
    filled = round(current / total * width)
    return "▓" * filled + "░" * (width - filled)


def callback_message(callback: CallbackQuery) -> Message | None:
    """Возвращает сообщение-канал для ответа или None.

    callback.message может быть None или недоступным (старое сообщение);
    пользователя при этом всегда берем из callback.from_user.
    """
    message = callback.message
    if isinstance(message, Message):
        return message
    return None


async def send_next_question(
    target_message: Message,
    session: AsyncSession,
    user_id: int,
    lesson_id: int,
    state: FSMContext | None = None,
) -> None:
    await target_message.bot.send_chat_action(chat_id=target_message.chat.id, action=ChatAction.TYPING)

    result = await get_next_exercise(session, lesson_id, user_id)
    if result is None:
        if state is not None:
            await state.clear()
        lesson = await get_lesson_summary(session, lesson_id, user_id)
        if lesson is None:
            await target_message.answer("Занятие не найдено. Попробуйте начать заново.")
            return
        # Сначала засчитываем серию за сегодня (Duolingo-style, с заморозкой),
        # затем проверяем достижения - чтобы рубеж серии открывался в тот же день.
        streak_res, streak = await register_daily_activity_for(session, user_id)
        newly = await check_new_achievements_for(session, user_id)
        settings = get_settings()
        answered_today = await count_user_answers_today(session, user_id)
        goal_line = daily_goal_line(answered_today, settings.daily_goal_questions)
        froze_note = streak_freeze_note(streak_res) if streak_res else None

        total = lesson.correct_answers + lesson.wrong_answers
        percent = round((lesson.correct_answers / total) * 100, 1) if total else 0

        # Диагностика: отдельный итог с разбором по темам.
        if lesson.lesson_type == "diagnostic":
            correct_all, total_all = await get_overall_accuracy(session, user_id)
            score = score_line(lesson.correct_answers, total) or score_line(correct_all, total_all)
            lines = [
                "🩺 <b>Диагностика пройдена</b>",
                "",
                f"Результат: <b>{lesson.correct_answers} из {total}</b>.",
            ]
            if score:
                lines.append(score)
            breakdown = await get_lesson_topic_breakdown(session, lesson_id, user_id)
            weak = [b for b in breakdown if b[2] and b[1] < b[2]]
            if weak:
                lines.append("")
                lines.append("<b>Над чем поработать в первую очередь:</b>")
                for title, corr, tot in sorted(weak, key=lambda x: (x[1] / x[2]) if x[2] else 0)[:3]:
                    lines.append(f"• {title}: {corr}/{tot}")
            lines.extend(["", goal_line, "", "Дальше - короткие ежедневные занятия. Подберу вопросы под твои слабые места."])
            await target_message.answer("\n".join(lines), reply_markup=diagnostic_finished_keyboard())
            if newly:
                await target_message.answer(format_new_achievements(newly))
            return

        bar = _progress_bar(lesson.correct_answers, total)
        lines = [
            "🏁 <b>Занятие пройдено</b>",
            "",
            f"{bar}  {lesson.correct_answers} из {total} верно  ({percent}%)",
        ]

        # Ориентир по баллу - по всей накопленной точности, а не по одному занятию.
        correct_all, total_all = await get_overall_accuracy(session, user_id)
        score = score_line(correct_all, total_all)
        if score:
            lines.append(score)

        lines.extend(["", goal_line])
        if streak >= 2:
            lines.append(f"🔥 Серия: <b>{streak}</b> дн. подряд. Не сбавляй.")
        elif streak == 1:
            lines.append("🌱 Первый день серии. Завтра закрепим.")
        if froze_note:
            lines.append(froze_note)

        if percent == 100:
            lines.extend(["", "Чисто, все верно. Так и держим."])
        elif percent >= 60:
            lines.extend(["", "Хороший результат. Ошибки вернутся на повтор - и закроются."])
        else:
            lines.extend(["", "Сложно - значит, есть куда расти. Загляни в «Мои ошибки»."])

        # Если уведомления выключены - мягко предложим включить прямо в финале.
        notifications_enabled = await get_notifications_enabled(session, user_id)
        try:
            share = share_dialog_url((await target_message.bot.me()).username or "bot", user_id)
        except Exception:  # noqa: BLE001
            share = None

        await target_message.answer(
            "\n".join(lines),
            reply_markup=lesson_finished_keyboard(notifications_enabled, share_url=share),
        )
        # Празднование рубежа серии - ярким отдельным сообщением (7, 30, 100 ...).
        if streak_res and streak_res.milestone:
            await target_message.answer(streak_milestone_text(streak_res.milestone))
        if newly:
            await target_message.answer(format_new_achievements(newly))
        return

    lesson, exercise = result
    answered = lesson.correct_answers + lesson.wrong_answers
    number = answered + 1
    options = sorted(exercise.options, key=lambda item: item.sort_order)
    bar = _progress_bar(answered, lesson.total_questions)
    stage_size = max(1, get_settings().lesson_questions_count)
    total_stages = max(1, -(-lesson.total_questions // stage_size))
    if total_stages > 1:
        stage_no = min(total_stages, answered // stage_size + 1)
        header = f"Этап {stage_no}/{total_stages} · вопрос {number}/{lesson.total_questions}"
    else:
        header = f"{number} / {lesson.total_questions}"
    text = f"{bar}  {header}\n\n{escape(exercise.question)}"

    # Формат ЕГЭ: вписать ответ с клавиатуры (а не выбрать вариант).
    if exercise.type == "text_input" and state is not None:
        await state.set_state(LessonStates.typing)
        await state.update_data(lesson_id=lesson.id, exercise_id=exercise.id)
        await target_message.answer(text + "\n\n<i>✍️ Впиши ответ сообщением.</i>")
        return

    # Обычный выбор варианта - на всякий случай выходим из режима ввода.
    if state is not None:
        await state.clear()
    if should_use_letters([option.option_text for option in options]):
        option_lines = [
            f"<b>{OPTION_LETTERS[index]})</b> {escape(option.option_text)}"
            for index, option in enumerate(options)
            if index < len(OPTION_LETTERS)
        ]
        text += "\n\n" + "\n".join(option_lines)
    await target_message.answer(text, reply_markup=exercise_options_keyboard(exercise, lesson.id))


async def start_lesson_for_user(
    target_message: Message,
    session: AsyncSession,
    telegram_user: TelegramUser,
    lesson_type: str = "daily",
    topic_id: int | None = None,
    state: FSMContext | None = None,
) -> None:
    await target_message.bot.send_chat_action(chat_id=target_message.chat.id, action=ChatAction.TYPING)

    user = await get_or_create_user(session, telegram_user)

    allowed, used, limit = await can_start_lesson(session, user)
    if not allowed:
        text = (
            "<b>На сегодня бесплатные вопросы закончились.</b>\n\n"
            f"Ты прошел <b>{used}</b> из <b>{limit}</b> на сегодня - хороший темп.\n\n"
            "В Premium лимита нет: занимайся сколько нужно, повторяй ошибки и открывай сложные темы."
        )
        await target_message.answer(text, reply_markup=premium_keyboard())
        return

    settings = get_settings()
    # Урок в стиле Duolingo: несколько этапов по lesson_questions_count вопросов.
    # Тренировка ошибок - один короткий этап.
    if lesson_type == "mistakes":
        base_count = settings.lesson_questions_count
    else:
        base_count = settings.lesson_questions_count * max(1, settings.lesson_stages)
    # Для бесплатного пользователя укорачиваем до остатка дневного лимита.
    questions_count = base_count
    if not is_active_premium(user):
        remaining = max(limit - used, 0)
        questions_count = max(1, min(base_count, remaining))

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

    stages = max(1, -(-lesson.total_questions // max(1, settings.lesson_questions_count)))
    if stages > 1:
        intro = f"Поехали! Урок из <b>{stages}</b> этапов по {settings.lesson_questions_count} вопросов. Разбор - после каждого ответа."
    else:
        intro = "Поехали. Несколько коротких вопросов - и разбор после каждого."
    await target_message.answer(intro)
    await send_next_question(target_message, session, user.id, lesson.id, state=state)


@router.message(F.text == "📚 Занятие дня")
async def daily_lesson_message(message: Message, session: AsyncSession, state: FSMContext) -> None:
    assert message.from_user is not None
    await start_lesson_for_user(message, session, message.from_user, lesson_type="daily", state=state)


@router.callback_query(F.data == "lesson:daily")
async def daily_lesson_callback(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    message = callback_message(callback)
    if message is None:
        await callback.answer("Не удалось открыть занятие. Напишите /start.", show_alert=True)
        return
    await callback.answer()
    await start_lesson_for_user(message, session, callback.from_user, lesson_type="daily", state=state)


@router.callback_query(F.data == "lesson:diagnostic")
async def diagnostic_callback(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    message = callback_message(callback)
    if message is None:
        await callback.answer("Напишите /start, чтобы продолжить.", show_alert=True)
        return
    await callback.answer()
    user = await get_or_create_user(session, callback.from_user)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    lesson = await create_diagnostic_lesson(session, user)
    if lesson is None:
        await message.answer("Пока недостаточно заданий для диагностики. Начни с «Занятия дня».")
        return
    await message.answer(
        "🩺 <b>Диагностика</b>: 7 вопросов из разных тем. Отвечай как есть - оценим стартовый балл и слабые места."
    )
    await send_next_question(message, session, user.id, lesson.id, state=state)


async def _send_path(target_message: Message, session: AsyncSession, telegram_user) -> None:
    user = await get_or_create_user(session, telegram_user)
    overview = await get_path_overview(session, user)
    lines = [
        "📍 <b>Твой путь к баллу ЕГЭ</b>",
        "",
        "Темы по порядку. Закрывай юниты - и прогноз балла растет.",
        "",
    ]
    topics = []
    for topic, mastery, total in overview:
        topics.append(topic)
        task = EGE_TASK_BY_SLUG.get(topic.slug, "")
        task_label = f" · ЕГЭ {task}" if task else ""
        emoji = topic.emoji or "•"
        if total <= 0:
            status = "⬜ не начато"
        else:
            status = f"{_progress_bar(int(mastery), 100)} {mastery:.0f}%"
        lines.append(f"{emoji} <b>{escape(topic.title)}</b>{task_label}\n{status}")
    await target_message.answer("\n".join(lines), reply_markup=topics_keyboard(topics))


@router.message(F.text == "📍 Мой путь")
async def path_message(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    await _send_path(message, session, message.from_user)


@router.callback_query(F.data == "path:show")
async def path_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    message = callback_message(callback)
    if message is None:
        await callback.answer("Напишите /start, чтобы открыть путь.", show_alert=True)
        return
    await _send_path(message, session, callback.from_user)
    await callback.answer()


@router.message(F.text == "🎯 Выбрать тему")
async def topic_list_message(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    user = await get_or_create_user(session, message.from_user)
    topics = await get_active_topics(session, include_premium=is_active_premium(user))
    await message.answer("Выберите тему:", reply_markup=topics_keyboard(topics))


@router.callback_query(F.data == "topics:list")
async def topic_list_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    message = callback_message(callback)
    if message is None:
        await callback.answer("Напишите /start, чтобы открыть меню.", show_alert=True)
        return
    user = await get_or_create_user(session, callback.from_user)
    topics = await get_active_topics(session, include_premium=is_active_premium(user))
    await message.answer("Выберите тему:", reply_markup=topics_keyboard(topics))
    await callback.answer()


@router.callback_query(F.data.startswith("topic:"))
async def topic_start_callback(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    message = callback_message(callback)
    if message is None:
        await callback.answer("Не удалось открыть тему. Напишите /start.", show_alert=True)
        return
    user = await get_or_create_user(session, callback.from_user)
    try:
        topic_id = int(callback.data.split(":", 1)[1])
    except (AttributeError, ValueError, IndexError):
        await message.answer("Тема не найдена.")
        await callback.answer()
        return

    topic = await get_topic(session, topic_id)
    if topic is None:
        await message.answer("Тема не найдена.")
        await callback.answer()
        return
    if topic.is_premium and not is_active_premium(user):
        await message.answer(
            "Эта тема входит в Premium. Бесплатно можно тренироваться в базовых разделах.",
            reply_markup=premium_keyboard(),
        )
        await callback.answer()
        return
    await callback.answer()
    await start_lesson_for_user(message, session, callback.from_user, lesson_type="topic", topic_id=topic.id, state=state)


@router.message(F.text == "🧩 Мои ошибки")
async def mistakes_lesson_message(message: Message, session: AsyncSession, state: FSMContext) -> None:
    assert message.from_user is not None
    await start_lesson_for_user(message, session, message.from_user, lesson_type="mistakes", state=state)


@router.callback_query(F.data == "lesson:mistakes")
async def mistakes_lesson_callback(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    message = callback_message(callback)
    if message is None:
        await callback.answer("Напишите /start, чтобы продолжить.", show_alert=True)
        return
    await callback.answer()
    await start_lesson_for_user(message, session, callback.from_user, lesson_type="mistakes", state=state)


@router.callback_query(F.data.startswith("ans:"))
async def answer_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    message = callback_message(callback)
    if message is None:
        await callback.answer("Не получилось обработать ответ. Напишите /start.", show_alert=True)
        return
    user = await get_or_create_user(session, callback.from_user)
    try:
        _, lesson_id_raw, exercise_id_raw, option_id_raw = callback.data.split(":")
        lesson_id = int(lesson_id_raw)
        exercise_id = int(exercise_id_raw)
        option_id = int(option_id_raw)
    except (AttributeError, ValueError):
        await message.answer("Не получилось обработать ответ. Попробуйте начать занятие заново.")
        await callback.answer()
        return

    result = await answer_exercise(session, user, lesson_id, exercise_id, option_id)
    if result is None:
        await message.answer("Не получилось обработать ответ. Попробуйте начать занятие заново.")
        await callback.answer()
        return

    # Снимаем клавиатуру с вопроса: ответ уже засчитан, повторные клики не нужны.
    try:
        await message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass

    if result.is_correct:
        verdict = "✅ Верно!"
    else:
        verdict = f"❌ Почти.  Правильный ответ: {escape(result.correct_option_text)}"
    lines = [f"<b>{verdict}</b>", "", escape(result.short_explanation)]
    if result.example_text:
        lines.extend(["", f"<b>Пример:</b> {escape(result.example_text)}"])
    if result.interesting_fact:
        lines.extend(["", f"<i>{escape(result.interesting_fact)}</i>"])
    if not result.is_correct:
        lines.extend(["", "<i>Вернусь с этим вопросом завтра - так правило закрепится.</i>"])

    # "Подробнее" имеет смысл только если есть отдельное развернутое объяснение.
    has_more = bool(result.full_explanation) and (
        result.full_explanation.strip() != result.short_explanation.strip()
    )
    await message.answer(
        "\n".join(lines),
        reply_markup=after_answer_keyboard(lesson_id, exercise_id, has_more=has_more),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("next:"))
async def next_question_callback(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    message = callback_message(callback)
    if message is None:
        await callback.answer("Напишите /start, чтобы продолжить.", show_alert=True)
        return
    user = await get_or_create_user(session, callback.from_user)
    try:
        lesson_id = int(callback.data.split(":", 1)[1])
    except (AttributeError, ValueError, IndexError):
        await message.answer("Занятие не найдено. Попробуйте начать заново.")
        await callback.answer()
        return
    await send_next_question(message, session, user.id, lesson_id, state=state)
    await callback.answer()


@router.message(LessonStates.typing, F.text, ~F.text.startswith("/"), ~F.text.in_(MAIN_MENU_LABELS))
async def lesson_text_answer(message: Message, session: AsyncSession, state: FSMContext) -> None:
    """Обработка ответа, введенного текстом (формат ЕГЭ «впиши слово»).

    Нажатия кнопок меню и команды (/...) исключены фильтром: они проходят
    в свои обработчики, а не засчитываются как ответ.
    """
    assert message.from_user is not None
    data = await state.get_data()
    await state.clear()
    lesson_id = data.get("lesson_id")
    exercise_id = data.get("exercise_id")
    if lesson_id is None or exercise_id is None:
        return
    user = await get_or_create_user(session, message.from_user)
    result = await answer_text_exercise(session, user, int(lesson_id), int(exercise_id), message.text or "")
    if result is None:
        await message.answer("Не получилось засчитать ответ. Начни занятие заново через меню.")
        return

    if result.is_correct:
        verdict = "✅ Верно!"
    else:
        verdict = f"❌ Почти.  Правильный ответ: {escape(result.correct_option_text)}"
    lines = [f"<b>{verdict}</b>", "", escape(result.short_explanation)]
    if result.example_text:
        lines.extend(["", f"<b>Пример:</b> {escape(result.example_text)}"])
    if result.interesting_fact:
        lines.extend(["", f"<i>{escape(result.interesting_fact)}</i>"])
    if not result.is_correct:
        lines.extend(["", "<i>Вернусь с этим вопросом завтра - так правило закрепится.</i>"])

    has_more = bool(result.full_explanation) and (
        result.full_explanation.strip() != result.short_explanation.strip()
    )
    await message.answer(
        "\n".join(lines),
        reply_markup=after_answer_keyboard(int(lesson_id), int(exercise_id), has_more=has_more),
    )


@router.callback_query(F.data.startswith("finish:"))
async def finish_lesson_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    message = callback_message(callback)
    if message is None:
        await callback.answer("Напишите /start, чтобы продолжить.", show_alert=True)
        return
    user = await get_or_create_user(session, callback.from_user)
    try:
        lesson_id = int(callback.data.split(":", 1)[1])
    except (AttributeError, ValueError, IndexError):
        await message.answer("Занятие не найдено.", reply_markup=main_menu_keyboard())
        await callback.answer()
        return
    lesson = await cancel_lesson(session, lesson_id, user.id)
    if lesson is None:
        await message.answer("Занятие не найдено.", reply_markup=main_menu_keyboard())
    else:
        await message.answer(
            "Занятие остановлено. Можно вернуться позже или начать новую короткую тренировку.",
            reply_markup=main_menu_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("full:"))
async def full_explanation_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    message = callback_message(callback)
    if message is None:
        await callback.answer("Напишите /start, чтобы продолжить.", show_alert=True)
        return
    try:
        exercise_id = int(callback.data.split(":", 1)[1])
    except (AttributeError, ValueError, IndexError):
        await message.answer("Не удалось открыть подробное объяснение.")
        await callback.answer()
        return
    exercise = await get_exercise_details(session, exercise_id)
    if exercise is None:
        await message.answer("Упражнение не найдено.")
    else:
        lines = ["<b>Подробное объяснение</b>", "", escape(exercise.full_explanation or exercise.short_explanation)]
        if exercise.example_text:
            lines.extend(["", f"<b>Пример:</b> {escape(exercise.example_text)}"])
        await message.answer("\n".join(lines))
    await callback.answer()


@router.callback_query(F.data.startswith("save_rule:"))
async def save_rule_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    message = callback_message(callback)
    if message is None:
        await callback.answer("Напишите /start, чтобы продолжить.", show_alert=True)
        return
    user = await get_or_create_user(session, callback.from_user)
    try:
        exercise_id = int(callback.data.split(":", 1)[1])
    except (AttributeError, ValueError, IndexError):
        await message.answer("Не удалось сохранить правило.")
        await callback.answer()
        return
    saved = await save_rule(session, user, exercise_id)
    if saved is None:
        await message.answer("Не удалось сохранить правило.")
    else:
        await message.answer("Сохранил. Правило теперь в разделе «Сохраненные правила» (⚙️ Настройки).")
    await callback.answer()


@router.callback_query(F.data == "rules:saved")
async def saved_rules_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    message = callback_message(callback)
    if message is None:
        await callback.answer("Напишите /start, чтобы продолжить.", show_alert=True)
        return
    user = await get_or_create_user(session, callback.from_user)
    rules = await get_saved_rules(session, user)
    if not rules:
        await message.answer(
            "Пока пусто. После ответа на вопрос нажмите «Сохранить правило» - "
            "и оно появится в вашей личной библиотеке."
        )
    else:
        lines = ["<b>📌 Сохраненные правила</b>", ""]
        for index, rule in enumerate(rules, start=1):
            lines.append(f"<b>{index}. {escape(rule.title)}</b>")
            lines.append(escape(rule.rule_text))
            lines.append("")
        await message.answer("\n".join(lines).strip())
    await callback.answer()


@router.message(F.text == "🏆 Мой прогресс")
async def progress_message(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    user = await get_or_create_user(session, message.from_user)
    text = await get_progress_text(session, user)
    await message.answer(text)


@router.callback_query(F.data == "progress:show")
async def progress_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    message = callback_message(callback)
    if message is None:
        await callback.answer("Напишите /start, чтобы продолжить.", show_alert=True)
        return
    user = await get_or_create_user(session, callback.from_user)
    text = await get_progress_text(session, user)
    await message.answer(text)
    await callback.answer()
