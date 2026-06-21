"""Парсинг ADMIN_TELEGRAM_IDS - известная критическая точка прошлых версий."""
from __future__ import annotations

import pytest

from app.config import Settings


def make_settings(admin_ids: str) -> Settings:
    return Settings(
        BOT_TOKEN="123456:TEST_TOKEN",
        DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/test",
        ADMIN_TELEGRAM_IDS=admin_ids,
    )


def test_single_admin_id() -> None:
    assert make_settings("123456789").admin_telegram_ids == [123456789]


def test_multiple_admin_ids() -> None:
    assert make_settings("123456789,987654321").admin_telegram_ids == [123456789, 987654321]


def test_empty_admin_ids() -> None:
    assert make_settings("").admin_telegram_ids == []


def test_admin_ids_with_spaces_and_trailing_comma() -> None:
    assert make_settings(" 1 , 2 ,").admin_telegram_ids == [1, 2]


def test_invalid_admin_ids_raise_readable_error() -> None:
    with pytest.raises(ValueError, match="ADMIN_TELEGRAM_IDS"):
        _ = make_settings("abc,123").admin_telegram_ids


def test_payments_and_ai_disabled_by_default() -> None:
    settings = make_settings("1")
    assert settings.enable_payments is False
    assert settings.enable_ai is False
