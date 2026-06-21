from __future__ import annotations

import json
from io import BytesIO
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.main import admin_keyboard
from app.config import get_settings
from app.db.models import Exercise
from app.services.admin_service import (
    ContentValidationError,
    create_exercise_from_payload,
    delete_exercise_by_id,
    export_exercises_json,
    get_admin_stats,
    get_exercise_text,
    get_topics_text,
    list_recent_exercises,
    search_exercises_text,
    set_exercise_status,
    update_exercise_from_payload,
)
from app.services.user_service import get_or_create_user

router = Router()


class AdminContentStates(StatesGroup):
    waiting_add_json = State()
    waiting_edit_id = State()
    waiting_edit_json = State()


def is_admin_telegram_id(telegram_id: int) -> bool:
    return telegram_id in get_settings().admin_telegram_ids


def exercise_json_template() -> str:
    payload = {
        "topic_slug": "governing",
        "level": "basic",
        "question": "Как правильно?",
        "options": [
            {"text": "Согласно приказу", "is_correct": True},
            {"text": "Согласно приказа", "is_correct": False},
        ],
        "short_explanation": "После «согласно» нужен дательный падеж: согласно чему? приказу.",
        "full_explanation": "Слово «согласно» требует дательного падежа: согласно приказу, договору, расписанию.",
        "example_text": "Согласно расписанию, встреча начнется в 10:00.",
        "interesting_fact": "Ошибка часто встречается в деловой речи.",
        "tags": ["управление", "падежи"],
        "difficulty_score": 2,
        "status": "published",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def _read_json_from_message(message: Message, bot: Bot) -> Any:
    if message.document:
        if not message.document.file_name or not message.document.file_name.endswith(".json"):
            raise ContentValidationError("Пришлите файл именно в формате .json")
        buffer = BytesIO()
        await bot.download(message.document, destination=buffer)
        buffer.seek(0)
        raw = buffer.read().decode("utf-8-sig")
    else:
        raw = message.text or ""
    if not raw.strip():
        raise ContentValidationError("JSON пустой. Пришлите объект или массив объектов.")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContentValidationError(f"JSON не читается: {exc.msg} на позиции {exc.pos}") from exc


async def _get_admin_user_id(session: AsyncSession, message: Message | CallbackQuery) -> int | None:
    tg_user = message.from_user
    if tg_user is None:
        return None
    user = await get_or_create_user(session, tg_user)
    return user.id


@router.message(Command("admin"))
async def admin_command(message: Message, session: AsyncSession, state: FSMContext) -> None:
    assert message.from_user is not None
    await state.clear()
    await get_or_create_user(session, message.from_user)

    if not is_admin_telegram_id(message.from_user.id):
        await message.answer("Админ-раздел закрыт. Тут без бейджа не пройти.")
        return

    await message.answer("<b>Админ-панель v2</b>\n\nВыберите действие:", reply_markup=admin_keyboard())


@router.callback_query(F.data == "admin:stats")
async def admin_stats(callback: CallbackQuery, session: AsyncSession) -> None:
    if not is_admin_telegram_id(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    text = await get_admin_stats(session)
    await callback.message.answer(text)
    await callback.answer()


@router.callback_query(F.data == "admin:topics")
async def admin_topics(callback: CallbackQuery, session: AsyncSession) -> None:
    if not is_admin_telegram_id(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.answer(await get_topics_text(session))
    await callback.answer()


@router.callback_query(F.data == "admin:recent_exercises")
async def admin_recent_exercises(callback: CallbackQuery, session: AsyncSession) -> None:
    if not is_admin_telegram_id(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.answer(await list_recent_exercises(session))
    await callback.answer()


@router.callback_query(F.data == "admin:exercises")
async def admin_exercises(callback: CallbackQuery, session: AsyncSession) -> None:
    if not is_admin_telegram_id(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    result = await session.execute(
        select(Exercise.status, func.count(Exercise.id)).group_by(Exercise.status).order_by(Exercise.status.asc())
    )
    rows = result.all()
    lines = ["<b>📚 Упражнения</b>", ""]
    if not rows:
        lines.append("Упражнений пока нет.")
    else:
        for status, count in rows:
            lines.append(f"• {status}: <b>{count}</b>")
    lines.extend([
        "",
        "Управление контентом:",
        "• <code>/add_exercise</code> - добавить одно или много упражнений JSON",
        "• <code>/edit_exercise ID</code> - исправить упражнение JSON-патчем",
        "• <code>/exercise ID</code> - посмотреть упражнение",
        "• <code>/search_exercises текст</code> - найти упражнения",
        "• <code>/export_exercises [статус]</code> - выгрузить в JSON-файл",
        "• <code>/publish_exercise ID</code> - опубликовать",
        "• <code>/archive_exercise ID</code> - убрать из выдачи",
        "• <code>/delete_exercise ID</code> - мягко удалить (история ответов сохраняется)",
    ])
    await callback.message.answer("\n".join(lines))
    await callback.answer()


@router.callback_query(F.data == "admin:add_exercise")
async def admin_add_exercise_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_telegram_id(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminContentStates.waiting_add_json)
    await callback.message.answer(
        "<b>Добавление упражнений</b>\n\n"
        "Пришлите JSON-объект или .json-файл. Можно прислать массив объектов для массового импорта.\n\n"
        "Шаблон:\n"
        f"<pre>{exercise_json_template()}</pre>"
    )
    await callback.answer()


@router.message(Command("add_exercise"))
async def admin_add_exercise_command(message: Message, state: FSMContext) -> None:
    assert message.from_user is not None
    if not is_admin_telegram_id(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    await state.set_state(AdminContentStates.waiting_add_json)
    await message.answer(
        "Пришлите JSON-объект, JSON-массив или .json-файл.\n\n"
        f"Шаблон:\n<pre>{exercise_json_template()}</pre>"
    )


@router.message(AdminContentStates.waiting_add_json)
async def admin_process_add_json(message: Message, bot: Bot, session: AsyncSession, state: FSMContext) -> None:
    assert message.from_user is not None
    if not is_admin_telegram_id(message.from_user.id):
        await message.answer("Нет доступа.")
        await state.clear()
        return
    try:
        payload = await _read_json_from_message(message, bot)
        items = payload if isinstance(payload, list) else [payload]
        if not all(isinstance(item, dict) for item in items):
            raise ContentValidationError("JSON должен быть объектом или массивом объектов.")
        admin_user_id = await _get_admin_user_id(session, message)
        created_ids: list[int] = []
        for item in items:
            exercise = await create_exercise_from_payload(session, item, admin_user_id=admin_user_id)
            created_ids.append(exercise.id)
        await message.answer(
            f"Готово. Добавлено упражнений: <b>{len(created_ids)}</b>.\n"
            f"ID: <code>{', '.join(map(str, created_ids[:30]))}</code>"
        )
        await state.clear()
    except ContentValidationError as exc:
        await message.answer(f"Не удалось добавить: {exc}")
    except Exception as exc:
        await message.answer(f"Неожиданная ошибка при импорте: {type(exc).__name__}: {exc}")


@router.callback_query(F.data == "admin:edit_exercise")
async def admin_edit_exercise_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_telegram_id(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminContentStates.waiting_edit_id)
    await callback.message.answer("Пришлите ID упражнения, которое нужно исправить. Например: <code>125</code>")
    await callback.answer()


@router.message(Command("edit_exercise"))
async def admin_edit_exercise_command(message: Message, state: FSMContext) -> None:
    assert message.from_user is not None
    if not is_admin_telegram_id(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].strip().isdigit():
        await state.update_data(edit_exercise_id=int(parts[1].strip()))
        await state.set_state(AdminContentStates.waiting_edit_json)
        await message.answer("Пришлите JSON-патч. Можно указать только поля, которые нужно заменить, например:\n<pre>{\"short_explanation\": \"Новый текст\"}</pre>")
        return
    await state.set_state(AdminContentStates.waiting_edit_id)
    await message.answer("Пришлите ID упражнения. Например: <code>125</code>")


@router.message(AdminContentStates.waiting_edit_id)
async def admin_process_edit_id(message: Message, state: FSMContext) -> None:
    assert message.from_user is not None
    if not is_admin_telegram_id(message.from_user.id):
        await message.answer("Нет доступа.")
        await state.clear()
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Нужен числовой ID упражнения. Например: <code>125</code>")
        return
    await state.update_data(edit_exercise_id=int(raw))
    await state.set_state(AdminContentStates.waiting_edit_json)
    await message.answer("Теперь пришлите JSON-патч. Можно отправить объект или .json-файл.\n\nПример:\n<pre>{\"status\": \"published\", \"short_explanation\": \"Новый текст объяснения\"}</pre>")


@router.message(AdminContentStates.waiting_edit_json)
async def admin_process_edit_json(message: Message, bot: Bot, session: AsyncSession, state: FSMContext) -> None:
    assert message.from_user is not None
    if not is_admin_telegram_id(message.from_user.id):
        await message.answer("Нет доступа.")
        await state.clear()
        return
    try:
        data = await state.get_data()
        exercise_id = int(data["edit_exercise_id"])
        payload = await _read_json_from_message(message, bot)
        if not isinstance(payload, dict):
            raise ContentValidationError("Для редактирования нужен один JSON-объект, не массив.")
        admin_user_id = await _get_admin_user_id(session, message)
        exercise = await update_exercise_from_payload(session, exercise_id, payload, admin_user_id=admin_user_id)
        await message.answer(f"Готово. Упражнение <b>#{exercise.id}</b> обновлено. Статус: <b>{exercise.status}</b>.")
        await state.clear()
    except ContentValidationError as exc:
        await message.answer(f"Не удалось исправить: {exc}")
    except Exception as exc:
        await message.answer(f"Неожиданная ошибка при редактировании: {type(exc).__name__}: {exc}")


async def _parse_exercise_id_from_command(message: Message, command_name: str) -> int | None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().isdigit():
        await message.answer(f"Формат: <code>/{command_name} ID</code>")
        return None
    return int(parts[1].strip())


@router.message(Command("publish_exercise"))
async def admin_publish_exercise(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    if not is_admin_telegram_id(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    exercise_id = await _parse_exercise_id_from_command(message, "publish_exercise")
    if exercise_id is None:
        return
    try:
        admin_user_id = await _get_admin_user_id(session, message)
        exercise = await set_exercise_status(session, exercise_id, "published", admin_user_id=admin_user_id)
        await message.answer(f"Упражнение <b>#{exercise.id}</b> опубликовано.")
    except ContentValidationError as exc:
        await message.answer(str(exc))


@router.message(Command("archive_exercise"))
async def admin_archive_exercise(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    if not is_admin_telegram_id(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    exercise_id = await _parse_exercise_id_from_command(message, "archive_exercise")
    if exercise_id is None:
        return
    try:
        admin_user_id = await _get_admin_user_id(session, message)
        exercise = await set_exercise_status(session, exercise_id, "archived", admin_user_id=admin_user_id)
        await message.answer(f"Упражнение <b>#{exercise.id}</b> отправлено в архив и больше не будет выдаваться пользователям.")
    except ContentValidationError as exc:
        await message.answer(str(exc))


@router.message(Command("delete_exercise"))
async def admin_delete_exercise(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    if not is_admin_telegram_id(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    exercise_id = await _parse_exercise_id_from_command(message, "delete_exercise")
    if exercise_id is None:
        return
    try:
        admin_user_id = await _get_admin_user_id(session, message)
        await delete_exercise_by_id(session, exercise_id, admin_user_id=admin_user_id)
        await message.answer(
            f"Упражнение <b>#{exercise_id}</b> помечено как удаленное.\n"
            "Оно больше не показывается пользователям, но история ответов и статистика сохранены."
        )
    except ContentValidationError as exc:
        await message.answer(str(exc))


@router.message(Command("exercise"))
async def admin_view_exercise(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    if not is_admin_telegram_id(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    exercise_id = await _parse_exercise_id_from_command(message, "exercise")
    if exercise_id is None:
        return
    try:
        await message.answer(await get_exercise_text(session, exercise_id))
    except ContentValidationError as exc:
        await message.answer(str(exc))


@router.message(Command("search_exercises"))
async def admin_search_exercises(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    if not is_admin_telegram_id(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await message.answer("Формат: <code>/search_exercises текст запроса</code>")
        return
    await message.answer(await search_exercises_text(session, parts[1].strip()))


@router.message(Command("export_exercises"))
async def admin_export_exercises(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    if not is_admin_telegram_id(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    parts = (message.text or "").split(maxsplit=1)
    status = parts[1].strip().lower() if len(parts) == 2 and parts[1].strip() else None
    try:
        json_text, count = await export_exercises_json(session, status=status)
    except ContentValidationError as exc:
        await message.answer(str(exc))
        return
    if count == 0:
        await message.answer("Нечего экспортировать: упражнений с такими условиями нет.")
        return
    suffix = f"_{status}" if status else ""
    document = BufferedInputFile(json_text.encode("utf-8"), filename=f"exercises{suffix}.json")
    await message.answer_document(document, caption=f"Экспортировано упражнений: {count}")


@router.callback_query(F.data == "admin:ai")
async def admin_ai(callback: CallbackQuery) -> None:
    if not is_admin_telegram_id(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.answer(
        "<b>🧠 AI-черновики</b>\n\n"
        "В этой версии AI пока выключен. Архитектурно оставлен безопасный сценарий: "
        "AI генерирует черновик → администратор проверяет → только потом публикация."
    )
    await callback.answer()


@router.callback_query(F.data == "admin:broadcast")
async def admin_broadcast(callback: CallbackQuery) -> None:
    if not is_admin_telegram_id(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.answer(
        "<b>📢 Рассылка</b>\n\n"
        "Раздел пока оставлен как безопасная заглушка, чтобы случайно не отправить сообщение всем пользователям."
    )
    await callback.answer()
