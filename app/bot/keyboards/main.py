from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.models import Exercise, Topic


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📚 Занятие дня"), KeyboardButton(text="🎯 Выбрать тему")],
            [KeyboardButton(text="🧩 Мои ошибки"), KeyboardButton(text="🏆 Мой прогресс")],
            [KeyboardButton(text="✨ Премиум"), KeyboardButton(text="⚙️ Настройки")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def onboarding_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📚 Начать занятие", callback_data="lesson:daily")
    builder.button(text="🎯 Выбрать цель", callback_data="profile:goals")
    builder.button(text="🧠 Выбрать тему", callback_data="topics:list")
    builder.button(text="🏆 Мой прогресс", callback_data="progress:show")
    builder.button(text="✨ Премиум", callback_data="premium:show")
    builder.adjust(1)
    return builder.as_markup()


def goals_keyboard() -> InlineKeyboardMarkup:
    goals = [
        ("Готовлюсь к ЕГЭ", "ege"),
        ("Готовлюсь к ОГЭ", "oge"),
        ("Хочу писать грамотнее", "write_better"),
        ("Хочу говорить красивее", "speak_better"),
        ("Просто люблю русский язык", "love_russian"),
        ("Пока не знаю", "unknown"),
    ]
    builder = InlineKeyboardBuilder()
    for text, code in goals:
        builder.button(text=text, callback_data=f"goal:{code}")
    builder.adjust(1)
    return builder.as_markup()


def levels_keyboard() -> InlineKeyboardMarkup:
    levels = [
        ("Школа", "school"),
        ("Студент", "student"),
        ("Взрослый", "adult"),
        ("Продвинутый", "advanced"),
        ("Определить позже", "later"),
    ]
    builder = InlineKeyboardBuilder()
    for text, code in levels:
        builder.button(text=text, callback_data=f"level:{code}")
    builder.adjust(1)
    return builder.as_markup()


def topics_keyboard(topics: list[Topic]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for topic in topics:
        premium_mark = " ✨" if topic.is_premium else ""
        emoji = topic.emoji or "•"
        builder.button(text=f"{emoji} {topic.title}{premium_mark}", callback_data=f"topic:{topic.id}")
    builder.button(text="🎲 Случайная тема", callback_data="lesson:daily")
    builder.adjust(1)
    return builder.as_markup()


def exercise_options_keyboard(exercise: Exercise, lesson_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    options = sorted(exercise.options, key=lambda item: item.sort_order)
    for option in options:
        builder.button(
            text=option.option_text,
            callback_data=f"ans:{lesson_id}:{exercise.id}:{option.id}",
        )
    builder.adjust(1)
    return builder.as_markup()


def after_answer_keyboard(lesson_id: int, exercise_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Следующий вопрос", callback_data=f"next:{lesson_id}")
    builder.button(text="Объяснить подробнее", callback_data=f"full:{exercise_id}")
    builder.button(text="Сохранить правило", callback_data=f"save_rule:{exercise_id}")
    builder.button(text="Завершить занятие", callback_data=f"finish:{lesson_id}")
    builder.adjust(1)
    return builder.as_markup()


def premium_keyboard(payments_enabled: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if payments_enabled:
        builder.button(text="Купить Premium на месяц ⭐", callback_data="premium:buy_month")
    else:
        builder.button(text="Премиум скоро", callback_data="premium:disabled")
    builder.button(text="Что входит в Premium", callback_data="premium:details")
    builder.button(text="Назад в меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def admin_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Статистика", callback_data="admin:stats")
    builder.button(text="📚 Упражнения", callback_data="admin:exercises")
    builder.button(text="➕ Добавить JSON", callback_data="admin:add_exercise")
    builder.button(text="✏️ Исправить JSON", callback_data="admin:edit_exercise")
    builder.button(text="🗂 Последние задания", callback_data="admin:recent_exercises")
    builder.button(text="🏷 Темы / topic_slug", callback_data="admin:topics")
    builder.button(text="🧠 AI-черновики", callback_data="admin:ai")
    builder.button(text="📢 Рассылка", callback_data="admin:broadcast")
    builder.adjust(1)
    return builder.as_markup()


def settings_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎯 Изменить цель", callback_data="profile:goals")
    builder.button(text="📈 Изменить уровень", callback_data="profile:levels")
    builder.button(text="🏠 Главное меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()
