"""Новое в v3.1: напоминания, кнопки А/Б/В."""
from __future__ import annotations

from datetime import datetime, time

from app.bot.keyboards.main import LETTERS_THRESHOLD, OPTION_LETTERS, should_use_letters
from app.services.notification_service import build_reminder_text, is_reminder_due


def test_short_options_keep_full_text_buttons() -> None:
    assert should_use_letters(["звОнит", "звонИт"]) is False


def test_long_options_switch_to_letters() -> None:
    options = ["Когда мы пришли домой, начался дождь и все промокли до нитки", "Короткий"]
    assert should_use_letters(options) is True


def test_threshold_boundary() -> None:
    assert should_use_letters(["а" * LETTERS_THRESHOLD]) is False
    assert should_use_letters(["а" * (LETTERS_THRESHOLD + 1)]) is True


def test_letters_enough_for_six_options() -> None:
    assert len(OPTION_LETTERS) >= 6


def test_reminder_due_in_moscow() -> None:
    # 07:00 UTC == 10:00 МСК
    now_utc = datetime(2026, 6, 12, 7, 0)
    assert is_reminder_due(time(10, 0), "Europe/Moscow", now_utc) is True
    assert is_reminder_due(time(10, 0), "Europe/Moscow", datetime(2026, 6, 12, 10, 0)) is False


def test_reminder_due_other_minute_false() -> None:
    assert is_reminder_due(time(10, 0), "Europe/Moscow", datetime(2026, 6, 12, 7, 1)) is False


def test_reminder_bad_timezone_falls_back() -> None:
    # Некорректная таймзона не должна ронять планировщик.
    assert is_reminder_due(time(10, 0), "Mars/Olympus", datetime(2026, 6, 12, 7, 0)) in (True, False)


def test_reminder_text_contains_fact() -> None:
    text = build_reminder_text("Слово «кофе» допускает средний род в разговорной речи.")
    assert "Языковая деталь дня" in text


def test_reminder_text_without_fact() -> None:
    text = build_reminder_text(None)
    assert "Языковая деталь дня" not in text and len(text) > 10


# ---- v3.2: велком-бонус и сводка ----
from datetime import timedelta

from app.db.models import User
from app.services.lesson_service import effective_daily_limit
from app.services.notification_service import _parse_report_time


def _user(created_days_ago: int | None) -> User:
    created = None if created_days_ago is None else datetime.utcnow() - timedelta(days=created_days_ago)
    return User(telegram_id=1, daily_question_limit=5, created_at=created)


def test_new_user_gets_double_limit() -> None:
    assert effective_daily_limit(_user(0)) == 10
    assert effective_daily_limit(_user(2)) == 10


def test_old_user_gets_base_limit() -> None:
    assert effective_daily_limit(_user(3)) == 5
    assert effective_daily_limit(_user(30)) == 5


def test_user_without_created_at_treated_as_new() -> None:
    assert effective_daily_limit(_user(None)) == 10


def test_report_time_parsing() -> None:
    assert _parse_report_time("09:00") == time(9, 0)
    assert _parse_report_time("21:30") == time(21, 30)
    assert _parse_report_time("мусор") == time(9, 0)


# ---- v3.3: интервальное повторение ----
from app.services.lesson_service import REVIEW_INTERVALS_DAYS, next_review_interval


def test_review_intervals_are_1_3_7() -> None:
    assert REVIEW_INTERVALS_DAYS == (1, 3, 7)


def test_next_review_interval_progression() -> None:
    assert next_review_interval(0) == timedelta(days=1)
    assert next_review_interval(1) == timedelta(days=3)
    assert next_review_interval(2) == timedelta(days=7)


def test_review_mastered_after_all_stages() -> None:
    assert next_review_interval(3) is None
    assert next_review_interval(10) is None
