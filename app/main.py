from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.handlers import admin, exam, fallback, lessons, premium, start
from app.bot.middlewares.database import DbSessionMiddleware
from app.config import get_settings
from app.db.init_db import init_database
from app.db.session import async_session_factory, engine
from app.services.notification_service import reminder_loop


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
    dp.include_router(exam.router)
    dp.include_router(fallback.router)

    await bot.delete_webhook(drop_pending_updates=True)

    # Кнопка запуска Mini App (если задан MINIAPP_URL). Без URL бот работает как обычно.
    if settings.miniapp_url:
        from aiogram.types import MenuButtonWebApp, WebAppInfo

        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Открыть приложение",
                    web_app=WebAppInfo(url=settings.miniapp_url),
                )
            )
            logging.info("Mini App кнопка установлена: %s", settings.miniapp_url)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Не удалось установить кнопку Mini App: %s", exc)

    reminder_task = asyncio.create_task(reminder_loop(bot))
    try:
        await dp.start_polling(bot)
    finally:
        reminder_task.cancel()
        # Закрываем пул соединений в том же event loop, где он был создан.
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
