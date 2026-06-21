"""Логика дневного лимита (границы суток) и проверки премиума."""
from __future__ import annotations

from datetime import datetime, timedelta

from app.db.models import User
from app.services.lesson_service import is_active_premium, today_bounds_utc


def make_user(**kwargs) -> User:
    defaults = {"telegram_id": 1, "is_premium": False, "premium_until": None}
    defaults.update(kwargs)
    return User(**defaults)


def test_today_bounds_are_24_hours() -> None:
    start, end = today_bounds_utc("Europe/Moscow")
    assert end - start == timedelta(days=1)


def test_today_bounds_respect_moscow_midnight() -> None:
    # Московская полночь = 21:00 UTC предыдущего дня (UTC+3, без перехода на летнее время).
    start, _ = today_bounds_utc("Europe/Moscow")
    assert start.hour == 21
    assert start.minute == 0


def test_today_bounds_utc_timezone() -> None:
    start, _ = today_bounds_utc("UTC")
    assert start.hour == 0


def test_free_user_is_not_premium() -> None:
    assert is_active_premium(make_user()) is False


def test_expired_premium_is_not_active() -> None:
    user = make_user(is_premium=True, premium_until=datetime.utcnow() - timedelta(days=1))
    assert is_active_premium(user) is False


def test_active_premium_with_date() -> None:
    user = make_user(is_premium=True, premium_until=datetime.utcnow() + timedelta(days=10))
    assert is_active_premium(user) is True


def test_manual_premium_without_expiry_is_active() -> None:
    user = make_user(is_premium=True, premium_until=None)
    assert is_active_premium(user) is True
