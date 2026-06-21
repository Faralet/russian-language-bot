from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = Field(alias="BOT_TOKEN")

    # Важно: храним как строку, а не list[int].
    # Pydantic-settings пытается парсить list из env как JSON, поэтому значение
    # ADMIN_TELEGRAM_IDS=123,456 падало при старте. Строка надежно работает
    # и для "123,456", и для "123", и для пустого значения.
    admin_telegram_ids_raw: str = Field(default="", alias="ADMIN_TELEGRAM_IDS")

    database_url: str = Field(alias="DATABASE_URL")

    app_timezone: str = Field(default="Europe/Moscow", alias="APP_TIMEZONE")
    free_daily_question_limit: int = Field(default=5, alias="FREE_DAILY_QUESTION_LIMIT")
    lesson_questions_count: int = Field(default=5, alias="LESSON_QUESTIONS_COUNT")
    # Урок в стиле Duolingo: несколько этапов по lesson_questions_count вопросов.
    lesson_stages: int = Field(default=3, alias="LESSON_STAGES")
    daily_goal_questions: int = Field(default=10, alias="DAILY_GOAL_QUESTIONS")
    run_db_init_on_startup: bool = Field(default=True, alias="RUN_DB_INIT_ON_STARTUP")

    enable_payments: bool = Field(default=False, alias="ENABLE_PAYMENTS")
    premium_month_stars: int = Field(default=299, alias="PREMIUM_MONTH_STARS")
    premium_month_days: int = Field(default=30, alias="PREMIUM_MONTH_DAYS")

    welcome_bonus_days: int = Field(default=3, alias="WELCOME_BONUS_DAYS")
    enable_admin_report: bool = Field(default=True, alias="ENABLE_ADMIN_REPORT")
    admin_report_time: str = Field(default="09:00", alias="ADMIN_REPORT_TIME")

    enable_ai: bool = Field(default=False, alias="ENABLE_AI")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str | None = Field(default=None, alias="OPENAI_MODEL")
    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")

    # URL Telegram Mini App. Пусто = кнопка приложения не показывается (бот работает как обычно).
    miniapp_url: str = Field(default="", alias="MINIAPP_URL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
    )

    @property
    def admin_telegram_ids(self) -> list[int]:
        value = self.admin_telegram_ids_raw.strip()
        if not value:
            return []
        result: list[int] = []
        for item in value.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                result.append(int(item))
            except ValueError as exc:
                raise ValueError(
                    "ADMIN_TELEGRAM_IDS должен содержать только Telegram ID через запятую, "
                    "например: ADMIN_TELEGRAM_IDS=123456789,987654321"
                ) from exc
        return result


@lru_cache
def get_settings() -> Settings:
    return Settings()
