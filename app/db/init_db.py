from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.data.seed_content import EXERCISES, TOPICS
from app.db.models import Base, Exercise, ExerciseOption, Topic
from app.db.session import async_session_factory, engine


logger = logging.getLogger(__name__)


async def create_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def apply_safe_migrations() -> None:
    """Мини-миграции для баз, созданных предыдущими версиями.

    create_all не меняет существующие таблицы, поэтому уникальный индекс
    против двойного засчитывания ответа добавляем отдельно и безопасно.
    """
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_user_lesson_exercise_answer "
                    "ON user_answers (user_id, lesson_id, exercise_id)"
                )
            )
            # Поля рефералки. create_all не меняет существующую таблицу users,
            # поэтому добавляем колонки отдельно и безопасно.
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by bigint"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_count integer NOT NULL DEFAULT 0"))
            # Серии и заморозка серии (Duolingo-style).
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS current_streak integer NOT NULL DEFAULT 0"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS longest_streak integer NOT NULL DEFAULT 0"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS streak_freezes integer NOT NULL DEFAULT 2"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_activity_on date"))
    except Exception as exc:  # noqa: BLE001
        # Если в старой базе уже есть дубли ответов, индекс не создастся.
        # Бот продолжит работать: защита на уровне приложения остается.
        logger.warning("Не удалось применить безопасные миграции: %s", exc)


async def seed_topics(session: AsyncSession) -> dict[str, Topic]:
    result = await session.execute(select(Topic))
    existing_topics = {topic.slug: topic for topic in result.scalars().all()}

    for item in TOPICS:
        slug = item["slug"]
        if slug in existing_topics:
            topic = existing_topics[slug]
            # Важно для обновлений: если в новой версии изменили название/описание темы,
            # оно подтянется без удаления базы.
            topic.title = item["title"]
            topic.description = item.get("description")
            topic.emoji = item.get("emoji")
            topic.level = item.get("level", "basic")
            topic.is_premium = item.get("is_premium", False)
            topic.is_exam_topic = item.get("is_exam_topic", False)
            topic.sort_order = item.get("sort_order", 100)
            topic.status = item.get("status", "active")
            continue
        topic = Topic(
            slug=slug,
            title=item["title"],
            description=item.get("description"),
            emoji=item.get("emoji"),
            level=item.get("level", "basic"),
            is_premium=item.get("is_premium", False),
            is_exam_topic=item.get("is_exam_topic", False),
            sort_order=item.get("sort_order", 100),
            status=item.get("status", "active"),
        )
        session.add(topic)

    await session.commit()

    result = await session.execute(select(Topic))
    return {topic.slug: topic for topic in result.scalars().all()}


async def seed_exercises(session: AsyncSession, topics_by_slug: dict[str, Topic]) -> None:
    """Идемпотентная загрузка seed-контента.

    В первой версии импорт останавливался, если в базе уже было хотя бы одно
    упражнение. Это мешало обновлениям: новая база правил не добавлялась на VPS
    без удаления PostgreSQL volume. Теперь проверяем уникальность по паре
    topic_id + question и добавляем только отсутствующие упражнения.
    """
    existing_result = await session.execute(select(Exercise).options(selectinload(Exercise.topic), selectinload(Exercise.options)))
    existing_keys = {
        (
            exercise.topic.slug if exercise.topic else "",
            exercise.question,
            tuple(sorted(option.option_text for option in exercise.options)),
        )
        for exercise in existing_result.scalars().unique().all()
    }

    now = datetime.utcnow()
    created = 0
    for item in EXERCISES:
        topic = topics_by_slug[item["topic_slug"]]
        key = (topic.slug, item["question"], tuple(sorted(option["text"] for option in item["options"])))
        # Если одинаковый вопрос уже есть в этой теме, не плодим дубли.
        # Для точечного изменения пользуемся /edit_exercise.
        if key in existing_keys:
            continue

        exercise = Exercise(
            topic_id=topic.id,
            source="manual",
            type=item.get("type", "single_choice"),
            level=item.get("level", "basic"),
            question=item["question"],
            short_explanation=item["short_explanation"],
            full_explanation=item.get("full_explanation"),
            example_text=item.get("example_text"),
            interesting_fact=item.get("interesting_fact"),
            exam_type=item.get("exam_type", "none"),
            tags=item.get("tags", []),
            status=item.get("status", "published"),
            difficulty_score=item.get("difficulty_score", 1),
            published_at=now if item.get("status", "published") == "published" else None,
        )
        session.add(exercise)
        await session.flush()

        for index, option in enumerate(item["options"], start=1):
            session.add(
                ExerciseOption(
                    exercise_id=exercise.id,
                    option_text=option["text"],
                    is_correct=option["is_correct"],
                    explanation=option.get("explanation"),
                    sort_order=index,
                )
            )
        existing_keys.add(key)
        created += 1

    if created:
        await session.commit()


# Шаблонный наполнитель из ранних версий контента (признак "сгенерировано ИИ").
# Эти строки повторялись в сотнях заданий, поэтому вычищаем их из базы.
GENERIC_FACTS = (
    "Маленькая языковая точность часто делает речь заметно сильнее.",
    "Ударение - маленькая деталь, которая быстро показывает уровень речи.",
)
FILLER_SUFFIX = " Это правило лучше запоминать не отдельно, а через живой пример."


async def cleanup_generated_filler() -> None:
    """Однократно (идемпотентно) убирает шаблонный наполнитель из заданий.

    1. Снимает одинаковый "интересный факт", повторявшийся в сотнях заданий.
    2. Срезает шаблонную приписку из подробного объяснения.
    3. Если после очистки подробное объяснение совпало с коротким - убирает
       дубль (None), чтобы кнопка "Подробнее" не показывала тот же текст.

    Запускается при старте: после первого прохода совпадений не остается,
    поэтому повторные запуски ничего не меняют.
    """
    try:
        async with engine.begin() as conn:
            res1 = await conn.execute(
                text(
                    "UPDATE exercises SET interesting_fact = NULL "
                    "WHERE interesting_fact IN (:f1, :f2)"
                ),
                {"f1": GENERIC_FACTS[0], "f2": GENERIC_FACTS[1]},
            )
            res2 = await conn.execute(
                text(
                    "UPDATE exercises "
                    "SET full_explanation = btrim(replace(full_explanation, :suffix, '')) "
                    "WHERE full_explanation LIKE :like"
                ),
                {"suffix": FILLER_SUFFIX, "like": f"%{FILLER_SUFFIX.strip()}%"},
            )
            res3 = await conn.execute(
                text(
                    "UPDATE exercises SET full_explanation = NULL "
                    "WHERE full_explanation IS NOT NULL "
                    "AND btrim(full_explanation) = btrim(short_explanation)"
                )
            )
        logger.info(
            "Очистка наполнителя: факты=%s, приписки=%s, дубли_подробного=%s",
            res1.rowcount, res2.rowcount, res3.rowcount,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось вычистить шаблонный наполнитель: %s", exc)


async def init_database() -> None:
    await create_tables()
    await apply_safe_migrations()
    async with async_session_factory() as session:
        topics_by_slug = await seed_topics(session)
        await seed_exercises(session, topics_by_slug)
    await cleanup_generated_filler()
