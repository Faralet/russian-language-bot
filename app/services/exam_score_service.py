from __future__ import annotations

"""Прогноз балла ЕГЭ по заданиям.

Считаем готовность по каждому блоку заданий на основе освоенности тем
(UserTopicProgress) и привязки тема -> задание ЕГЭ -> первичные баллы.
Это честный ориентир по практике пользователя, а не официальный прогноз:
сочинение (№27) бот не оценивает, и не все задания покрыты контентом.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Topic, User, UserTopicProgress

# Тема -> (подпись с номером задания ЕГЭ, первичные баллы части 1, покрываемые темой).
TOPIC_TASK: dict[str, tuple[str, int]] = {
    "stress": ("№4 · ударения", 1),
    "paronyms": ("№5 · паронимы", 1),
    "speech_accuracy": ("№6 · лексические нормы", 1),
    "governing": ("№7 · грамматические нормы", 1),
    "syntax": ("№8 · синтаксические нормы", 2),
    "spelling": ("№9-15 · орфография", 7),
    "punctuation": ("№16-21 · пунктуация", 6),
}
PART1_MAX = 28   # первичных за часть 1 (краткие ответы)
ESSAY_MAX = 22   # №27, сочинение
TOTAL_MAX = 50   # первичных за всю работу


async def task_readiness(session: AsyncSession, user: User) -> tuple[list[dict], float, int]:
    """Возвращает (список заданий с готовностью, прогноз первичных, покрытые баллы)."""
    rows = (await session.execute(
        select(Topic.slug, UserTopicProgress.mastery_score, UserTopicProgress.total_answers)
        .join(UserTopicProgress, UserTopicProgress.topic_id == Topic.id)
        .where(UserTopicProgress.user_id == user.id)
    )).all()
    progress = {slug: (float(m or 0), int(n or 0)) for slug, m, n in rows}

    items: list[dict] = []
    projected = 0.0
    covered = 0
    for slug, (label, points) in TOPIC_TASK.items():
        mastery, answers = progress.get(slug, (0.0, 0))
        if answers > 0:
            projected += (mastery / 100.0) * points
            covered += points
        items.append({
            "slug": slug, "label": label, "points": points,
            "mastery": mastery, "answers": answers,
        })
    return items, projected, covered


def _mark(mastery: float) -> str:
    if mastery >= 80:
        return "✅"
    if mastery >= 60:
        return "🟡"
    return "🔴"


def format_score_report(items: list[dict], projected: float, covered: int) -> str:
    lines = ["📊 <b>Прогноз балла по заданиям ЕГЭ</b>", ""]
    if covered == 0:
        lines.append("Пока мало практики. Пройди занятия по темам - и здесь появится готовность по каждому заданию.")
        return "\n".join(lines)

    lines.append(
        f"Часть 1 (краткие ответы): ориентир <b>~{projected:.0f}</b> из {covered} "
        f"возможных первичных по освоенным темам (вся часть 1 - {PART1_MAX} баллов)."
    )
    lines.extend(["", "<b>Готовность по заданиям:</b>"])
    for it in items:
        if it["answers"] == 0:
            lines.append(f"⬜ {it['label']} — не начато")
        else:
            lines.append(f"{_mark(it['mastery'])} {it['label']} — {it['mastery']:.0f}%")

    weak = [it for it in items if it["answers"] > 0 and it["mastery"] < 80]
    weak.sort(key=lambda x: x["mastery"])
    if weak:
        names = ", ".join(w["label"].split(" · ")[0] for w in weak[:3])
        lines.extend(["", f"⚡ Быстрее всего балл вырастет здесь: <b>{names}</b>"])

    lines.extend([
        "",
        f"<i>Сочинение (№27, до {ESSAY_MAX} баллов) бот пока не оценивает - тренируй в разделе «📝 Сочинение».</i>",
        "<i>Это ориентир по твоей практике в боте, а не официальный прогноз ЕГЭ.</i>",
    ])
    return "\n".join(lines)
