from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AiDraftRequest:
    topic: str
    level: str
    count: int


class AiServiceDisabledError(RuntimeError):
    pass


async def generate_exercise_drafts(_: AiDraftRequest) -> list[dict]:
    """MVP-заглушка.

    На следующем этапе сюда подключается AI-контур:
    1. строгий системный промпт;
    2. JSON Schema для упражнений;
    3. валидация ответа;
    4. сохранение результата в ai_drafts;
    5. публикация только после ручной проверки админом.
    """
    raise AiServiceDisabledError(
        "AI-генерация отключена в MVP. Включите ENABLE_AI=true и реализуйте провайдера в app/services/ai_service.py."
    )
