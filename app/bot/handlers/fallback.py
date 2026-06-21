from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.main import main_menu_keyboard
from app.services.user_service import get_or_create_user

router = Router()


@router.message()
async def fallback_message(message: Message, session: AsyncSession) -> None:
    if message.from_user is not None:
        await get_or_create_user(session, message.from_user)
    await message.answer(
        "Я пока лучше всего работаю через кнопки меню.\n\n"
        "Выберите занятие, тему, ошибки или прогресс.",
        reply_markup=main_menu_keyboard(),
    )
