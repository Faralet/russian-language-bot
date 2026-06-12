from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.main import (
    goals_keyboard,
    levels_keyboard,
    main_menu_keyboard,
    onboarding_keyboard,
    settings_keyboard,
)
from app.services.user_service import get_or_create_user, set_goal, set_level

router = Router()

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
async def cmd_start(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    await get_or_create_user(session, message.from_user)

    text = (
        "<b>Добро пожаловать.</b>\n\n"
        "Здесь русский язык становится точнее, богаче и красивее.\n\n"
        "Каждый день - короткое занятие: вопрос, ответ, объяснение и маленький языковой инсайт."
    )
    await message.answer(text, reply_markup=main_menu_keyboard())
    await message.answer("С чего начнем?", reply_markup=onboarding_keyboard())


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
        "Можно спокойно поменять траекторию. Русский язык не обидится."
    )
    await message.answer(text, reply_markup=settings_keyboard())


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
