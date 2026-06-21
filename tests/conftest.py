"""Общая подготовка тестового окружения.

Настройки приложения читаются из переменных окружения, поэтому
обязательные значения задаем до импорта app.config.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("ADMIN_TELEGRAM_IDS", "123456789")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
