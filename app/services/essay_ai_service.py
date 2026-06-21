from __future__ import annotations

"""AI-проверка сочинения №27 по критериям ФИПИ К1-К10.

Включается флагом ENABLE_AI + ключ OPENAI_API_KEY. По умолчанию выключено,
поэтому ничего не ломает в проде, пока владелец не настроит ключ.

ВАЖНО: оценка ИИ - предварительная, ориентировочная. Официальный балл ставит
эксперт. Это явно сообщается пользователю.
"""

import json
import logging

from app.config import get_settings

logger = logging.getLogger(__name__)

CRITERIA_MAX = {
    "К1": 1, "К2": 3, "К3": 2, "К4": 1, "К5": 2,
    "К6": 1, "К7": 3, "К8": 3, "К9": 3, "К10": 3,
}
TOTAL_MAX = 22

SYSTEM_PROMPT = (
    "Ты - строгий эксперт ЕГЭ по русскому языку. Оцени сочинение (задание 27) "
    "по критериям ФИПИ К1-К10. Максимумы: К1=1, К2=3, К3=2, К4=1, К5=2, К6=1, "
    "К7=3, К8=3, К9=3, К10=3 (итого 22). Будь честным и строгим, как на реальной "
    "проверке. Если объем меньше 150 слов - ставь 0 по всем критериям. "
    "Верни СТРОГО JSON без пояснений вокруг: "
    '{"scores": {"К1": 0, "К2": 0, "К3": 0, "К4": 0, "К5": 0, "К6": 0, "К7": 0, '
    '"К8": 0, "К9": 0, "К10": 0}, "comments": {"К2": "...", "К7": "..."}, '
    '"summary": "короткий вывод и что улучшить"}. '
    "В comments давай короткие замечания только по проблемным критериям."
)


def ai_available() -> bool:
    settings = get_settings()
    return bool(settings.enable_ai and settings.openai_api_key)


async def grade_essay(essay_text: str, source_text: str = "") -> str:
    """Возвращает отформатированный разбор по К1-К10 или понятное сообщение об ошибке."""
    settings = get_settings()
    if not ai_available():
        return (
            "🤖 AI-проверка пока выключена. Включается владельцем (ENABLE_AI + ключ). "
            "Сейчас проверь себя по критериям в разделе «📝 Сочинение»."
        )

    import aiohttp

    model = settings.openai_model or "gpt-4o-mini"
    base_url = (settings.openai_base_url or "https://api.openai.com/v1").rstrip("/")
    user_content = (
        f"Исходный текст (если приведен):\n{source_text or '(не приведен)'}\n\n"
        f"Сочинение ученика:\n{essay_text}"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("AI grade HTTP %s: %s", resp.status, body[:300])
                    return "🤖 Не удалось получить оценку (ошибка сервиса). Попробуй позже."
                data = await resp.json()
        content = data["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI grade failed: %s", exc)
        return "🤖 Не удалось получить оценку (сеть/таймаут). Попробуй позже."

    return _format_feedback(content)


def _format_feedback(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        obj = json.loads(raw)
        scores = obj.get("scores", {})
        comments = obj.get("comments", {})
        summary = obj.get("summary", "")
    except Exception:  # noqa: BLE001
        # Модель вернула не JSON - отдаем как есть, с пометкой.
        return "🤖 <b>Предварительная оценка ИИ</b>\n\n" + raw + "\n\n<i>Это ориентир, не официальный балл.</i>"

    total = 0
    lines = ["🤖 <b>Предварительная оценка ИИ по К1-К10</b>", ""]
    for crit, max_pts in CRITERIA_MAX.items():
        val = scores.get(crit)
        try:
            val = int(val)
        except (TypeError, ValueError):
            val = 0
        val = max(0, min(max_pts, val))
        total += val
        mark = "✅" if val == max_pts else ("🟡" if val > 0 else "🔴")
        line = f"{mark} {crit}: {val}/{max_pts}"
        note = comments.get(crit)
        if note:
            line += f" - {note}"
        lines.append(line)
    lines.append("")
    lines.append(f"Итого (ориентир): <b>{total} из {TOTAL_MAX}</b>.")
    if summary:
        lines.extend(["", summary])
    lines.append("")
    lines.append("<i>Это предварительная оценка ИИ. Официальный балл ставит эксперт.</i>")
    return "\n".join(lines)
