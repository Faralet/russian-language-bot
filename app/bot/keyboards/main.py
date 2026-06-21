from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.models import Exercise, Topic


# Ярлыки кнопок главного меню. Используются, чтобы не засчитывать нажатие
# кнопки меню как текстовый ответ, когда пользователь в режиме ввода ответа.
MAIN_MENU_LABELS = frozenset({
    "📚 Занятие дня",
    "📍 Мой путь",
    "🎯 Выбрать тему",
    "🧩 Мои ошибки",
    "📝 Сочинение",
    "📊 Мой балл",
    "🎓 ОГЭ",
    "🏆 Мой прогресс",
    "👥 Пригласить друга",
    "✨ Премиум",
    "⚙️ Настройки",
})


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📚 Занятие дня"), KeyboardButton(text="📍 Мой путь")],
            [KeyboardButton(text="🎯 Выбрать тему"), KeyboardButton(text="🧩 Мои ошибки")],
            [KeyboardButton(text="📝 Сочинение"), KeyboardButton(text="📊 Мой балл")],
            [KeyboardButton(text="🎓 ОГЭ"), KeyboardButton(text="🏆 Мой прогресс")],
            [KeyboardButton(text="👥 Пригласить друга"), KeyboardButton(text="✨ Премиум")],
            [KeyboardButton(text="⚙️ Настройки")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def onboarding_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🩺 Узнать свой стартовый балл", callback_data="lesson:diagnostic")
    builder.button(text="📚 Начать занятие", callback_data="lesson:daily")
    builder.button(text="🎯 Выбрать цель", callback_data="profile:goals")
    builder.button(text="👥 Пригласить друга", callback_data="invite:show")
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


OPTION_LETTERS = ["А", "Б", "В", "Г", "Д", "Е"]
# Telegram обрезает текст кнопок (~64 символа). Если вариант длинный,
# показываем варианты в тексте вопроса, а на кнопках - только буквы.
LETTERS_THRESHOLD = 32


def should_use_letters(option_texts: list[str]) -> bool:
    return any(len(text) > LETTERS_THRESHOLD for text in option_texts)


def exercise_options_keyboard(exercise: Exercise, lesson_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    options = sorted(exercise.options, key=lambda item: item.sort_order)
    use_letters = should_use_letters([option.option_text for option in options])
    for index, option in enumerate(options):
        label = OPTION_LETTERS[index] if use_letters and index < len(OPTION_LETTERS) else option.option_text
        builder.button(
            text=label,
            callback_data=f"ans:{lesson_id}:{exercise.id}:{option.id}",
        )
    if use_letters:
        builder.adjust(len(options) if len(options) <= 4 else 3)
    else:
        builder.adjust(1)
    return builder.as_markup()


def after_answer_keyboard(lesson_id: int, exercise_id: int, has_more: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Дальше →", callback_data=f"next:{lesson_id}")
    # Кнопку "Подробнее" показываем только если есть что добавить к короткому разбору.
    if has_more:
        builder.button(text="📖 Подробнее", callback_data=f"full:{exercise_id}")
    builder.button(text="⭐ В сохраненные", callback_data=f"save_rule:{exercise_id}")
    builder.button(text="Завершить", callback_data=f"finish:{lesson_id}")
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


def lesson_finished_keyboard(notifications_enabled: bool = True, share_url: str | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📚 Еще занятие", callback_data="lesson:daily")
    builder.button(text="🧩 Повторить ошибки", callback_data="lesson:mistakes")
    if share_url:
        builder.button(text="📣 Поделиться результатом", url=share_url)
    builder.button(text="👥 Пригласить друга", callback_data="invite:show")
    if not notifications_enabled:
        builder.button(text="🔔 Напоминать каждый день", callback_data="notify:on")
    builder.button(text="🏠 Меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def invite_keyboard(share_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📣 Отправить приглашение другу", url=share_url)
    builder.button(text="🏠 Меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def diagnostic_finished_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🚀 Начать ежедневную подготовку", callback_data="lesson:daily")
    builder.button(text="🎯 Выбрать цель", callback_data="profile:goals")
    builder.button(text="👥 Пригласить друга", callback_data="invite:show")
    builder.button(text="🏠 Меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def reminder_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📚 Начать занятие", callback_data="lesson:daily")
    builder.button(text="🔕 Отключить напоминания", callback_data="notify:off")
    builder.adjust(1)
    return builder.as_markup()


def notifications_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if enabled:
        builder.button(text="🔕 Выключить", callback_data="notify:off")
    else:
        builder.button(text="🔔 Включить", callback_data="notify:on")
    for hhmm in ("08:00", "10:00", "13:00", "19:00", "21:00"):
        builder.button(text=hhmm, callback_data=f"notify:time:{hhmm}")
    builder.button(text="⬅️ Назад в настройки", callback_data="settings:show")
    builder.adjust(1, 5, 1)
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
    builder.button(text="🔔 Напоминания", callback_data="notify:menu")
    builder.button(text="📌 Сохраненные правила", callback_data="rules:saved")
    builder.button(text="🏠 Главное меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()
