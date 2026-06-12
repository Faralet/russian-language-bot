from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.handlers import admin, fallback, lessons, premium, start
from app.bot.middlewares.database import DbSessionMiddleware
from app.config import get_settings
from app.db.init_db import init_database
from app.db.session import async_session_factory, engine


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    if settings.run_db_init_on_startup:
        await init_database()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(DbSessionMiddleware(async_session_factory))

    dp.include_router(start.router)
    dp.include_router(lessons.router)
    dp.include_router(premium.router)
    dp.include_router(admin.router)
    dp.include_router(fallback.router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        asyncio.run(engine.dispose())
