from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.main import main_menu_keyboard, premium_keyboard
from app.config import get_settings
from app.services.payment_service import activate_premium_month
from app.services.user_service import get_or_create_user

router = Router()

PREMIUM_TEXT = (
    "<b>✨ Premium</b>\n\n"
    "Premium превращает бота из ежедневной разминки в полноценный личный тренажер.\n\n"
    "Что входит:\n"
    "• занятия без дневного лимита;\n"
    "• все темы, включая экзаменационные;\n"
    "• тренировка ошибок;\n"
    "• подробные объяснения;\n"
    "• расширенная статистика;\n"
    "• будущий AI-разбор ваших предложений.\n\n"
    "Бесплатный режим остается мягким: 1 короткая тренировка в день."
)


@router.message(F.text == "✨ Премиум")
async def premium_message(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    user = await get_or_create_user(session, message.from_user)
    status = ""
    if user.is_premium and user.premium_until:
        status = f"\n\nВаш Premium активен до: <b>{user.premium_until:%d.%m.%Y}</b>."
    settings = get_settings()
    await message.answer(PREMIUM_TEXT + status, reply_markup=premium_keyboard(settings.enable_payments))


@router.callback_query(F.data == "premium:show")
async def premium_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    assert callback.from_user is not None
    user = await get_or_create_user(session, callback.from_user)
    status = ""
    if user.is_premium and user.premium_until:
        status = f"\n\nВаш Premium активен до: <b>{user.premium_until:%d.%m.%Y}</b>."
    settings = get_settings()
    await callback.message.answer(PREMIUM_TEXT + status, reply_markup=premium_keyboard(settings.enable_payments))
    await callback.answer()


@router.callback_query(F.data == "premium:details")
async def premium_details_callback(callback: CallbackQuery) -> None:
    settings = get_settings()
    await callback.message.answer(PREMIUM_TEXT, reply_markup=premium_keyboard(settings.enable_payments))
    await callback.answer()


@router.callback_query(F.data == "premium:disabled")
async def premium_disabled_callback(callback: CallbackQuery) -> None:
    await callback.message.answer(
        "<b>Премиум пока не подключен.</b>\n\n"
        "Сейчас бот работает в тестовом режиме без реальных платежей. "
        "Когда продуктовая логика будет проверена, сюда можно будет подключить Telegram Stars или другой платежный сценарий."
    )
    await callback.answer()


@router.callback_query(F.data == "premium:buy_month")
async def buy_premium_month(callback: CallbackQuery, session: AsyncSession) -> None:
    assert callback.from_user is not None
    await get_or_create_user(session, callback.from_user)
    settings = get_settings()
    if not settings.enable_payments:
        await callback.message.answer(
            "<b>Платежи отключены.</b>\n\n"
            "Тестируем обучение, меню, прогресс и админку. Премиум-платежи подключим отдельным этапом."
        )
        await callback.answer()
        return

    # Для цифровых товаров Telegram использует Stars, валюта XTR.
    # В тестовом запуске платежный контур можно оставить как черновой и включить после настройки BotFather.
    await callback.message.answer_invoice(
        title="Premium Month",
        description="Полный доступ к занятиям на 30 дней.",
        payload="premium_month",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="Premium Month", amount=settings.premium_month_stars)],
    )
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout_query_handler(pre_checkout_query: PreCheckoutQuery) -> None:
    if pre_checkout_query.invoice_payload != "premium_month":
        await pre_checkout_query.answer(ok=False, error_message="Неизвестный платежный пакет.")
        return
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment_handler(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    user = await get_or_create_user(session, message.from_user)
    payment = message.successful_payment
    subscription = await activate_premium_month(
        session=session,
        user=user,
        provider="telegram_stars",
        provider_payment_id=payment.telegram_payment_charge_id,
        amount=payment.total_amount,
        currency=payment.currency,
        raw_payload=payment.model_dump(mode="json"),
    )
    await message.answer(
        "<b>Premium активирован.</b>\n\n"
        f"Доступ действует до: <b>{subscription.expires_at:%d.%m.%Y}</b>.\n\n"
        "Теперь можно заниматься без дневного лимита.",
        reply_markup=main_menu_keyboard(),
    )
