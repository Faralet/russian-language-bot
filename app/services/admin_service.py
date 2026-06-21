from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import AdminLog, Exercise, ExerciseOption, Lesson, Payment, Topic, User, UserAnswer


class ContentValidationError(ValueError):
    pass


REQUIRED_EXERCISE_FIELDS = {"topic_slug", "question", "options", "short_explanation"}
ALLOWED_STATUSES = {"draft", "published", "archived", "rejected", "deleted"}
ALLOWED_LEVELS = {"basic", "intermediate", "advanced", "exam"}
ALLOWED_TYPES = {"single_choice"}


def _as_bool(value: Any) -> bool:
    return bool(value) if isinstance(value, bool) else str(value).lower() in {"1", "true", "yes", "да"}


def validate_options(options: Any) -> None:
    if not isinstance(options, list) or len(options) < 2:
        raise ContentValidationError("Поле options должно быть списком минимум из двух вариантов.")

    correct_count = 0
    texts: list[str] = []
    for option in options:
        if not isinstance(option, dict):
            raise ContentValidationError("Каждый вариант ответа должен быть объектом JSON.")
        text = str(option.get("text", "")).strip()
        if not text:
            raise ContentValidationError("У каждого варианта должен быть непустой text.")
        texts.append(text)
        if _as_bool(option.get("is_correct", False)):
            correct_count += 1

    if len(set(texts)) != len(texts):
        raise ContentValidationError("Варианты ответа не должны повторяться.")
    if correct_count != 1:
        raise ContentValidationError("У упражнения должен быть ровно один правильный вариант: is_correct=true.")


def validate_exercise_payload(payload: dict[str, Any]) -> None:
    missing = REQUIRED_EXERCISE_FIELDS - set(payload)
    if missing:
        raise ContentValidationError("Не хватает полей: " + ", ".join(sorted(missing)))

    if not str(payload.get("question", "")).strip():
        raise ContentValidationError("Поле question не должно быть пустым.")
    if not str(payload.get("short_explanation", "")).strip():
        raise ContentValidationError("Поле short_explanation не должно быть пустым.")

    level = str(payload.get("level", "basic")).strip()
    if level not in ALLOWED_LEVELS:
        raise ContentValidationError(f"level должен быть одним из: {', '.join(sorted(ALLOWED_LEVELS))}.")

    exercise_type = str(payload.get("type", "single_choice")).strip()
    if exercise_type not in ALLOWED_TYPES:
        raise ContentValidationError(f"type должен быть одним из: {', '.join(sorted(ALLOWED_TYPES))}.")

    tags = payload.get("tags")
    if tags is not None and (not isinstance(tags, list) or any(not str(tag).strip() for tag in tags)):
        raise ContentValidationError("tags должен быть списком непустых строк.")

    validate_options(payload.get("options"))


async def get_admin_stats(session: AsyncSession) -> str:
    users_count = int((await session.execute(select(func.count(User.id)))).scalar() or 0)
    premium_count = int((await session.execute(select(func.count(User.id)).where(User.is_premium.is_(True)))).scalar() or 0)
    topics_count = int((await session.execute(select(func.count(Topic.id)))).scalar() or 0)
    exercises_count = int((await session.execute(select(func.count(Exercise.id)))).scalar() or 0)
    published_count = int((await session.execute(select(func.count(Exercise.id)).where(Exercise.status == "published"))).scalar() or 0)
    draft_count = int((await session.execute(select(func.count(Exercise.id)).where(Exercise.status == "draft"))).scalar() or 0)
    lessons_count = int((await session.execute(select(func.count(Lesson.id)))).scalar() or 0)
    answers_count = int((await session.execute(select(func.count(UserAnswer.id)))).scalar() or 0)
    payments_count = int((await session.execute(select(func.count(Payment.id)).where(Payment.status == "succeeded"))).scalar() or 0)

    correct_count = int((await session.execute(select(func.count(UserAnswer.id)).where(UserAnswer.is_correct.is_(True)))).scalar() or 0)
    accuracy = round((correct_count / answers_count) * 100, 1) if answers_count else 0

    return "\n".join(
        [
            "<b>📊 Статистика v2</b>",
            "",
            f"Пользователей: <b>{users_count}</b>",
            f"Premium: <b>{premium_count}</b>",
            f"Тем: <b>{topics_count}</b>",
            f"Упражнений всего: <b>{exercises_count}</b>",
            f"Опубликовано: <b>{published_count}</b>",
            f"Черновиков: <b>{draft_count}</b>",
            f"Занятий: <b>{lessons_count}</b>",
            f"Ответов: <b>{answers_count}</b>",
            f"Точность ответов: <b>{accuracy}%</b>",
            f"Успешных платежей: <b>{payments_count}</b>",
        ]
    )


async def get_topics_text(session: AsyncSession) -> str:
    result = await session.execute(select(Topic).order_by(Topic.sort_order.asc(), Topic.title.asc()))
    topics = result.scalars().all()
    lines = ["<b>Темы и topic_slug для импорта</b>", ""]
    for topic in topics:
        lines.append(f"• <code>{topic.slug}</code> - {topic.emoji or ''} {topic.title}")
    return "\n".join(lines)


async def list_recent_exercises(session: AsyncSession, limit: int = 12) -> str:
    result = await session.execute(
        select(Exercise)
        .options(selectinload(Exercise.topic))
        .order_by(Exercise.id.desc())
        .limit(limit)
    )
    exercises = result.scalars().all()
    if not exercises:
        return "Упражнений пока нет."

    lines = ["<b>Последние упражнения</b>", ""]
    for ex in exercises:
        topic_title = ex.topic.title if ex.topic else "без темы"
        question = ex.question.replace("\n", " ")[:70]
        lines.append(f"<b>#{ex.id}</b> [{ex.status}] {topic_title}: {question}")
    lines.extend([
        "",
        "Команды:",
        "<code>/publish_exercise ID</code>",
        "<code>/archive_exercise ID</code>",
        "<code>/delete_exercise ID</code>",
        "<code>/edit_exercise ID</code>",
    ])
    return "\n".join(lines)


async def create_exercise_from_payload(
    session: AsyncSession,
    payload: dict[str, Any],
    admin_user_id: int | None = None,
) -> Exercise:
    validate_exercise_payload(payload)

    topic_slug = str(payload["topic_slug"]).strip()
    topic_result = await session.execute(select(Topic).where(Topic.slug == topic_slug))
    topic = topic_result.scalar_one_or_none()
    if topic is None:
        raise ContentValidationError(f"Тема topic_slug='{topic_slug}' не найдена. Откройте /admin → Темы.")

    status = str(payload.get("status", "published")).strip() or "published"
    if status not in ALLOWED_STATUSES:
        raise ContentValidationError("status должен быть draft, published, archived, rejected или deleted.")

    exercise = Exercise(
        topic_id=topic.id,
        author_id=admin_user_id,
        source=str(payload.get("source", "manual")),
        type=str(payload.get("type", "single_choice")),
        level=str(payload.get("level", "basic")),
        question=str(payload["question"]).strip(),
        short_explanation=str(payload["short_explanation"]).strip(),
        full_explanation=str(payload.get("full_explanation") or payload["short_explanation"]).strip(),
        example_text=str(payload.get("example_text") or "").strip() or None,
        interesting_fact=str(payload.get("interesting_fact") or "").strip() or None,
        exam_type=str(payload.get("exam_type", "none")),
        tags=list(payload.get("tags") or []),
        status=status,
        difficulty_score=int(payload.get("difficulty_score", 2)),
        published_at=datetime.utcnow() if status == "published" else None,
    )
    session.add(exercise)
    await session.flush()

    for index, option in enumerate(payload["options"], start=1):
        session.add(
            ExerciseOption(
                exercise_id=exercise.id,
                option_text=str(option["text"]).strip(),
                is_correct=_as_bool(option.get("is_correct", False)),
                explanation=str(option.get("explanation") or "").strip() or None,
                sort_order=int(option.get("sort_order", index)),
            )
        )

    session.add(AdminLog(admin_user_id=admin_user_id, action="create_exercise", entity_type="exercise", entity_id=exercise.id, details={"source": "admin_json"}))
    await session.commit()
    await session.refresh(exercise)
    return exercise


async def update_exercise_from_payload(
    session: AsyncSession,
    exercise_id: int,
    payload: dict[str, Any],
    admin_user_id: int | None = None,
) -> Exercise:
    result = await session.execute(select(Exercise).where(Exercise.id == exercise_id).options(selectinload(Exercise.options)))
    exercise = result.scalar_one_or_none()
    if exercise is None:
        raise ContentValidationError(f"Упражнение #{exercise_id} не найдено.")

    if "topic_slug" in payload:
        topic_slug = str(payload["topic_slug"]).strip()
        topic_result = await session.execute(select(Topic).where(Topic.slug == topic_slug))
        topic = topic_result.scalar_one_or_none()
        if topic is None:
            raise ContentValidationError(f"Тема topic_slug='{topic_slug}' не найдена.")
        exercise.topic_id = topic.id

    simple_fields = [
        "source", "type", "level", "question", "short_explanation", "full_explanation",
        "example_text", "interesting_fact", "exam_type", "status", "difficulty_score",
    ]
    for field in simple_fields:
        if field not in payload:
            continue
        value = payload[field]
        if field == "difficulty_score":
            setattr(exercise, field, int(value))
        elif field == "status":
            status = str(value)
            if status not in ALLOWED_STATUSES:
                raise ContentValidationError("status должен быть draft, published, archived, rejected или deleted.")
            exercise.status = status
            if status == "published" and exercise.published_at is None:
                exercise.published_at = datetime.utcnow()
        elif field == "level":
            level = str(value).strip()
            if level not in ALLOWED_LEVELS:
                raise ContentValidationError(f"level должен быть одним из: {', '.join(sorted(ALLOWED_LEVELS))}.")
            exercise.level = level
        elif field == "type":
            exercise_type = str(value).strip()
            if exercise_type not in ALLOWED_TYPES:
                raise ContentValidationError(f"type должен быть одним из: {', '.join(sorted(ALLOWED_TYPES))}.")
            exercise.type = exercise_type
        elif field in {"question", "short_explanation"}:
            text_value = str(value).strip() if value is not None else ""
            if not text_value:
                raise ContentValidationError(f"Поле {field} не должно быть пустым.")
            setattr(exercise, field, text_value)
        else:
            setattr(exercise, field, str(value).strip() if value is not None else None)

    if "tags" in payload:
        tags = payload.get("tags") or []
        if not isinstance(tags, list) or any(not str(tag).strip() for tag in tags):
            raise ContentValidationError("tags должен быть списком непустых строк.")
        exercise.tags = list(tags)

    if "options" in payload:
        validate_options(payload["options"])
        await session.execute(delete(ExerciseOption).where(ExerciseOption.exercise_id == exercise.id))
        for index, option in enumerate(payload["options"], start=1):
            session.add(
                ExerciseOption(
                    exercise_id=exercise.id,
                    option_text=str(option["text"]).strip(),
                    is_correct=_as_bool(option.get("is_correct", False)),
                    explanation=str(option.get("explanation") or "").strip() or None,
                    sort_order=int(option.get("sort_order", index)),
                )
            )

    session.add(AdminLog(admin_user_id=admin_user_id, action="update_exercise", entity_type="exercise", entity_id=exercise.id, details={"source": "admin_json"}))
    await session.commit()
    await session.refresh(exercise)
    return exercise


async def set_exercise_status(session: AsyncSession, exercise_id: int, status: str, admin_user_id: int | None = None) -> Exercise:
    result = await session.execute(select(Exercise).where(Exercise.id == exercise_id))
    exercise = result.scalar_one_or_none()
    if exercise is None:
        raise ContentValidationError(f"Упражнение #{exercise_id} не найдено.")
    if status not in ALLOWED_STATUSES:
        raise ContentValidationError("Недопустимый статус.")
    exercise.status = status
    if status == "published" and exercise.published_at is None:
        exercise.published_at = datetime.utcnow()
    session.add(AdminLog(admin_user_id=admin_user_id, action=f"set_status_{status}", entity_type="exercise", entity_id=exercise.id, details={}))
    await session.commit()
    await session.refresh(exercise)
    return exercise


async def delete_exercise_by_id(session: AsyncSession, exercise_id: int, admin_user_id: int | None = None) -> None:
    """Мягкое удаление.

    Физическое удаление каскадом стирало варианты ответа и исторические
    ответы пользователей, ломая статистику и прогресс. Поэтому упражнение
    переводится в status='deleted' и исчезает из выдачи, но история остается.
    """
    result = await session.execute(select(Exercise).where(Exercise.id == exercise_id))
    exercise = result.scalar_one_or_none()
    if exercise is None:
        raise ContentValidationError(f"Упражнение #{exercise_id} не найдено.")
    exercise.status = "deleted"
    session.add(AdminLog(admin_user_id=admin_user_id, action="soft_delete_exercise", entity_type="exercise", entity_id=exercise_id, details={}))
    await session.commit()


def _format_exercise_details(exercise: Exercise) -> str:
    options_lines = []
    for option in sorted(exercise.options, key=lambda item: item.sort_order):
        mark = "✅" if option.is_correct else "▫️"
        options_lines.append(f"{mark} {option.option_text}")

    topic_title = exercise.topic.title if exercise.topic else "без темы"
    lines = [
        f"<b>Упражнение #{exercise.id}</b> [{exercise.status}]",
        "",
        f"Тема: {topic_title}",
        f"Уровень: {exercise.level} | Тип: {exercise.type} | Сложность: {exercise.difficulty_score}",
        "",
        f"<b>Вопрос:</b> {exercise.question}",
        "",
        *options_lines,
        "",
        f"<b>Объяснение:</b> {exercise.short_explanation}",
    ]
    if exercise.example_text:
        lines.append(f"<b>Пример:</b> {exercise.example_text}")
    if exercise.tags:
        lines.append(f"Теги: {', '.join(exercise.tags)}")
    lines.extend([
        "",
        f"Показов: {exercise.usage_count} | Верно: {exercise.correct_count} | Неверно: {exercise.wrong_count}",
    ])
    return "\n".join(lines)


async def get_exercise_text(session: AsyncSession, exercise_id: int) -> str:
    result = await session.execute(
        select(Exercise)
        .where(Exercise.id == exercise_id)
        .options(selectinload(Exercise.options), selectinload(Exercise.topic))
    )
    exercise = result.scalar_one_or_none()
    if exercise is None:
        raise ContentValidationError(f"Упражнение #{exercise_id} не найдено.")
    return _format_exercise_details(exercise)


async def search_exercises_text(session: AsyncSession, query: str, limit: int = 10) -> str:
    pattern = f"%{query.strip()}%"
    result = await session.execute(
        select(Exercise)
        .join(Topic, Topic.id == Exercise.topic_id)
        .options(selectinload(Exercise.topic))
        .where(
            Exercise.status != "deleted",
            (
                Exercise.question.ilike(pattern)
                | Exercise.short_explanation.ilike(pattern)
                | Exercise.full_explanation.ilike(pattern)
                | Topic.title.ilike(pattern)
                | func.array_to_string(Exercise.tags, " ").ilike(pattern)
            ),
        )
        .order_by(Exercise.id.desc())
        .limit(limit)
    )
    exercises = result.scalars().all()
    if not exercises:
        return f"По запросу «{query}» ничего не найдено."

    lines = [f"<b>Найдено по запросу «{query}»</b> (до {limit}):", ""]
    for ex in exercises:
        topic_title = ex.topic.title if ex.topic else "без темы"
        question = ex.question.replace("\n", " ")[:70]
        lines.append(f"<b>#{ex.id}</b> [{ex.status}] {topic_title}: {question}")
    lines.extend(["", "Подробности: <code>/exercise ID</code>"])
    return "\n".join(lines)


async def export_exercises_json(session: AsyncSession, status: str | None = None) -> tuple[str, int]:
    """Выгружает упражнения в JSON-строку, совместимую с импортом /add_exercise."""
    import json

    stmt = (
        select(Exercise)
        .options(selectinload(Exercise.options), selectinload(Exercise.topic))
        .order_by(Exercise.id.asc())
    )
    if status:
        if status not in ALLOWED_STATUSES:
            raise ContentValidationError("Недопустимый статус для экспорта.")
        stmt = stmt.where(Exercise.status == status)
    else:
        stmt = stmt.where(Exercise.status != "deleted")

    result = await session.execute(stmt)
    exercises = result.scalars().unique().all()

    payload = []
    for ex in exercises:
        payload.append(
            {
                "id": ex.id,
                "topic_slug": ex.topic.slug if ex.topic else None,
                "type": ex.type,
                "level": ex.level,
                "question": ex.question,
                "options": [
                    {"text": o.option_text, "is_correct": o.is_correct, "explanation": o.explanation}
                    for o in sorted(ex.options, key=lambda item: item.sort_order)
                ],
                "short_explanation": ex.short_explanation,
                "full_explanation": ex.full_explanation,
                "example_text": ex.example_text,
                "interesting_fact": ex.interesting_fact,
                "exam_type": ex.exam_type,
                "tags": list(ex.tags or []),
                "status": ex.status,
                "difficulty_score": ex.difficulty_score,
            }
        )
    return json.dumps(payload, ensure_ascii=False, indent=2), len(payload)
