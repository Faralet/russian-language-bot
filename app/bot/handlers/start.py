from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from datetime import time

from app.bot.keyboards.main import (
    goals_keyboard,
    invite_keyboard,
    levels_keyboard,
    main_menu_keyboard,
    notifications_keyboard,
    onboarding_keyboard,
    settings_keyboard,
)
from app.services.referral_service import (
    REFERRAL_REWARD_DAYS,
    build_invite_text,
    parse_ref_payload,
    referral_link,
    share_dialog_url,
    try_register_referral,
)
from app.services.user_service import (
    get_or_create_user,
    get_user_by_telegram_id,
    set_goal,
    set_level,
    set_notification_time,
    set_notifications_enabled,
)

router = Router()


async def _bot_username(message: Message) -> str:
    me = await message.bot.me()
    return me.username or "bot"


async def show_invite(message: Message, session: AsyncSession, telegram_user) -> None:
    """Показывает экран приглашения с персональной ссылкой и кнопкой «Поделиться»."""
    user = await get_or_create_user(session, telegram_user)
    username = await _bot_username(message)
    link = referral_link(username, user.id)
    text = build_invite_text(user.referral_count or 0) + f"\n\n<code>{link}</code>"
    await message.answer(text, reply_markup=invite_keyboard(share_dialog_url(username, user.id)))

GOAL_TITLES = {
    "ege": "Готовлюсь к ЕГЭ",
    "oge": "Готовлюсь к ОГЭ",
    "write_better": "Хочу писать грамотнее",
    "speak_better": "Хочу говорить красивее",
    "love_russian": "Просто люблю русский язык",
    "unknown": "Пока не знаю",
}

LEVEL_TITLES = {
    "school": "Школа",
    "student": "Студент",
    "adult": "Взрослый",
    "advanced": "Продвинутый",
    "later": "Определить позже",
}


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, session: AsyncSession) -> None:
    assert message.from_user is not None

    # Новый ли пользователь - нужно для реферальной привязки (только при первом старте).
    existing = await get_user_by_telegram_id(session, message.from_user.id)
    is_new = existing is None
    user = await get_or_create_user(session, message.from_user)

    referred_note = ""
    if is_new:
        referrer_id = parse_ref_payload(command.args)
        if referrer_id is not None:
            referrer = await try_register_referral(session, user, referrer_id)
            if referrer is not None:
                referred_note = (
                    f"\n\n🎁 Ты пришел по приглашению - вам обоим начислено "
                    f"<b>+{REFERRAL_REWARD_DAYS} дней полного доступа</b>."
                )

    text = (
        "<b>Привет! Это «Точный русский» - тренажер ЕГЭ и ОГЭ.</b>\n\n"
        "📚 Задания в формате ФИПИ: ударения, паронимы, нормы, орфография, пунктуация.\n"
        "⚡ Короткий разбор после каждого вопроса - что за правило и где ловушка.\n"
        "📈 Прогресс - в баллах, а не в пустых процентах.\n"
        "🔥 Серия дней, достижения и цель на каждый день.\n\n"
        "10 минут в день - и балл растет."
        + referred_note
    )
    await message.answer(text, reply_markup=main_menu_keyboard())
    await message.answer(
        "С чего начнем? Советую короткую диагностику - узнаешь свой стартовый балл.",
        reply_markup=onboarding_keyboard(),
    )


@router.message(F.text == "👥 Пригласить друга")
async def invite_message(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    await show_invite(message, session, message.from_user)


@router.callback_query(F.data == "invite:show")
async def invite_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    assert callback.from_user is not None
    if isinstance(callback.message, Message):
        await show_invite(callback.message, session, callback.from_user)
    await callback.answer()


@router.callback_query(F.data == "menu:main")
async def show_main_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    assert callback.from_user is not None
    await get_or_create_user(session, callback.from_user)
    await callback.message.answer("Главное меню под рукой.", reply_markup=main_menu_keyboard())
    await callback.answer()


@router.message(F.text == "⚙️ Настройки")
async def settings_message(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    user = await get_or_create_user(session, message.from_user)
    goal = GOAL_TITLES.get(user.goal or "", user.goal or "не выбрана")
    level = LEVEL_TITLES.get(user.level or "", user.level or "не выбран")
    text = (
        "<b>⚙️ Настройки</b>\n\n"
        f"Цель: <b>{goal}</b>\n"
        f"Уровень: <b>{level}</b>\n\n"
        "Цель и уровень можно поменять в любой момент - подбор вопросов подстроится."
    )
    await message.answer(text, reply_markup=settings_keyboard())


def _notifications_text(user) -> str:
    state = "включены" if user.notifications_enabled else "выключены"
    return (
        "<b>🔔 Напоминания</b>\n\n"
        f"Сейчас напоминания <b>{state}</b>.\n"
        f"Время: <b>{user.notification_time:%H:%M}</b> ({user.timezone}).\n\n"
        "Одно короткое сообщение в день - и серия занятий не прервется."
    )


@router.callback_query(F.data == "settings:show")
async def settings_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    assert callback.from_user is not None
    user = await get_or_create_user(session, callback.from_user)
    goal = GOAL_TITLES.get(user.goal or "", user.goal or "не выбрана")
    level = LEVEL_TITLES.get(user.level or "", user.level or "не выбран")
    await callback.message.answer(
        f"<b>⚙️ Настройки</b>\n\nЦель: <b>{goal}</b>\nУровень: <b>{level}</b>",
        reply_markup=settings_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "notify:menu")
async def notifications_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    assert callback.from_user is not None
    user = await get_or_create_user(session, callback.from_user)
    await callback.message.answer(_notifications_text(user), reply_markup=notifications_keyboard(user.notifications_enabled))
    await callback.answer()


@router.callback_query(F.data == "notify:on")
async def notifications_on(callback: CallbackQuery, session: AsyncSession) -> None:
    assert callback.from_user is not None
    user = await get_or_create_user(session, callback.from_user)
    user = await set_notifications_enabled(session, user, True)
    await callback.message.answer(
        f"Готово. Буду напоминать раз в день в <b>{user.notification_time:%H:%M}</b>.\n"
        "Поменять время можно в ⚙️ Настройки → 🔔 Напоминания.",
    )
    await callback.answer()


@router.callback_query(F.data == "notify:off")
async def notifications_off(callback: CallbackQuery, session: AsyncSession) -> None:
    assert callback.from_user is not None
    user = await get_or_create_user(session, callback.from_user)
    await set_notifications_enabled(session, user, False)
    await callback.message.answer(
        "Напоминания выключены. Вернуть их можно в ⚙️ Настройки → 🔔 Напоминания."
    )
    await callback.answer()


@router.callback_query(F.data.startswith("notify:time:"))
async def notifications_time(callback: CallbackQuery, session: AsyncSession) -> None:
    assert callback.from_user is not None
    user = await get_or_create_user(session, callback.from_user)
    try:
        raw = callback.data.split(":", 2)[2]
        hours, minutes = raw.split(":")
        value = time(int(hours), int(minutes))
    except (ValueError, IndexError, AttributeError):
        await callback.answer("Не удалось разобрать время", show_alert=True)
        return
    user = await set_notification_time(session, user, value)
    await callback.message.answer(
        f"Время напоминаний: <b>{user.notification_time:%H:%M}</b>. Напоминания включены."
    )
    await callback.answer()


@router.callback_query(F.data == "profile:goals")
async def choose_goal(callback: CallbackQuery) -> None:
    await callback.message.answer("Выберите цель обучения:", reply_markup=goals_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("goal:"))
async def process_goal(callback: CallbackQuery, session: AsyncSession) -> None:
    assert callback.from_user is not None
    user = await get_or_create_user(session, callback.from_user)
    goal_code = callback.data.split(":", 1)[1]
    await set_goal(session, user, goal_code)
    goal_title = GOAL_TITLES.get(goal_code, goal_code)
    await callback.message.answer(
        f"Отлично. Цель сохранена: <b>{goal_title}</b>.\n\nТеперь выберите уровень:",
        reply_markup=levels_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "profile:levels")
async def choose_level(callback: CallbackQuery) -> None:
    await callback.message.answer("Выберите уровень:", reply_markup=levels_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("level:"))
async def process_level(callback: CallbackQuery, session: AsyncSession) -> None:
    assert callback.from_user is not None
    user = await get_or_create_user(session, callback.from_user)
    level_code = callback.data.split(":", 1)[1]
    await set_level(session, user, level_code)
    level_title = LEVEL_TITLES.get(level_code, level_code)
    await callback.message.answer(
        f"Уровень сохранен: <b>{level_title}</b>.\n\nМожно начинать тренировку.",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()
