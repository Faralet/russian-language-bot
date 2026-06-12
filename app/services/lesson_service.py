from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db.models import (
    Exercise,
    ExerciseOption,
    Lesson,
    LessonExercise,
    SavedRule,
    Topic,
    User,
    UserAnswer,
    UserTopicProgress,
)


@dataclass
class AnswerResult:
    is_correct: bool
    correct_option_text: str
    short_explanation: str
    full_explanation: str | None
    example_text: str | None
    interesting_fact: str | None


async def count_user_answers_today(session: AsyncSession, user_id: int) -> int:
    today = date.today()
    start = datetime.combine(today, time.min)
    end = start + timedelta(days=1)
    result = await session.execute(
        select(func.count(UserAnswer.id)).where(
            UserAnswer.user_id == user_id,
            UserAnswer.answered_at >= start,
            UserAnswer.answered_at < end,
        )
    )
    return int(result.scalar() or 0)


async def can_start_lesson(session: AsyncSession, user: User) -> tuple[bool, int, int]:
    if user.is_premium and user.premium_until and user.premium_until > datetime.utcnow():
        return True, 0, 999999
    used = await count_user_answers_today(session, user.id)
    limit = user.daily_question_limit or get_settings().free_daily_question_limit
    return used < limit, used, limit


async def get_active_topics(session: AsyncSession, include_premium: bool = False) -> list[Topic]:
    stmt = select(Topic).where(Topic.status == "active", Topic.slug != "daily")
    if not include_premium:
        stmt = stmt.where(Topic.is_premium.is_(False))
    stmt = stmt.order_by(Topic.sort_order.asc(), Topic.title.asc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_topic(session: AsyncSession, topic_id: int) -> Topic | None:
    result = await session.execute(select(Topic).where(Topic.id == topic_id, Topic.status == "active"))
    return result.scalar_one_or_none()


async def create_lesson(
    session: AsyncSession,
    user: User,
    lesson_type: str = "daily",
    topic_id: int | None = None,
    questions_count: int | None = None,
) -> Lesson | None:
    settings = get_settings()
    questions_count = questions_count or settings.lesson_questions_count

    topic_filter = []
    if topic_id is not None:
        topic_filter.append(Exercise.topic_id == topic_id)

    # Free users do not receive premium topics in ordinary lessons.
    if not (user.is_premium and user.premium_until and user.premium_until > datetime.utcnow()):
        topic_filter.append(Topic.is_premium.is_(False))

    result = await session.execute(
        select(Exercise)
        .join(Topic, Topic.id == Exercise.topic_id)
        .where(
            Exercise.status == "published",
            Topic.status == "active",
            *topic_filter,
        )
        .order_by(func.random())
        .limit(questions_count)
    )
    exercises = list(result.scalars().all())

    if not exercises:
        return None

    lesson = Lesson(
        user_id=user.id,
        topic_id=topic_id,
        lesson_type=lesson_type,
        status="started",
        total_questions=len(exercises),
    )
    session.add(lesson)
    await session.flush()

    for index, exercise in enumerate(exercises, start=1):
        session.add(
            LessonExercise(
                lesson_id=lesson.id,
                exercise_id=exercise.id,
                sort_order=index,
            )
        )
        exercise.usage_count += 1

    await session.commit()
    await session.refresh(lesson)
    return lesson


async def create_mistakes_lesson(
    session: AsyncSession,
    user: User,
    questions_count: int | None = None,
) -> Lesson | None:
    settings = get_settings()
    questions_count = questions_count or settings.lesson_questions_count
    wrong_exercises_result = await session.execute(
        select(Exercise)
        .join(UserAnswer, UserAnswer.exercise_id == Exercise.id)
        .where(
            UserAnswer.user_id == user.id,
            UserAnswer.is_correct.is_(False),
            Exercise.status == "published",
        )
        .order_by(UserAnswer.answered_at.desc())
        .limit(questions_count)
    )
    exercises = list(dict.fromkeys(wrong_exercises_result.scalars().all()))

    if len(exercises) < 3:
        return await create_lesson(session, user, lesson_type="random", questions_count=questions_count)

    lesson = Lesson(
        user_id=user.id,
        lesson_type="mistakes",
        status="started",
        total_questions=len(exercises),
    )
    session.add(lesson)
    await session.flush()

    for index, exercise in enumerate(exercises, start=1):
        session.add(LessonExercise(lesson_id=lesson.id, exercise_id=exercise.id, sort_order=index))
        exercise.usage_count += 1

    await session.commit()
    await session.refresh(lesson)
    return lesson


async def get_next_exercise(session: AsyncSession, lesson_id: int, user_id: int) -> tuple[Lesson, Exercise] | None:
    lesson_result = await session.execute(select(Lesson).where(Lesson.id == lesson_id, Lesson.user_id == user_id))
    lesson = lesson_result.scalar_one_or_none()
    if lesson is None or lesson.status != "started":
        return None

    answered_result = await session.execute(
        select(UserAnswer.exercise_id).where(UserAnswer.lesson_id == lesson_id, UserAnswer.user_id == user_id)
    )
    answered_ids = set(answered_result.scalars().all())

    stmt = (
        select(Exercise)
        .join(LessonExercise, LessonExercise.exercise_id == Exercise.id)
        .where(LessonExercise.lesson_id == lesson_id)
        .options(selectinload(Exercise.options))
        .order_by(LessonExercise.sort_order.asc())
    )
    result = await session.execute(stmt)
    exercises = list(result.scalars().unique().all())

    for exercise in exercises:
        if exercise.id not in answered_ids:
            return lesson, exercise

    lesson.status = "completed"
    lesson.completed_at = datetime.utcnow()
    await session.commit()
    await session.refresh(lesson)
    return None


async def answer_exercise(
    session: AsyncSession,
    user: User,
    lesson_id: int,
    exercise_id: int,
    selected_option_id: int,
) -> AnswerResult | None:
    lesson_result = await session.execute(
        select(Lesson).where(
            Lesson.id == lesson_id,
            Lesson.user_id == user.id,
            Lesson.status == "started",
        )
    )
    lesson = lesson_result.scalar_one_or_none()
    if lesson is None:
        return None

    lesson_exercise_result = await session.execute(
        select(LessonExercise.id).where(
            LessonExercise.lesson_id == lesson_id,
            LessonExercise.exercise_id == exercise_id,
        )
    )
    if lesson_exercise_result.scalar_one_or_none() is None:
        return None

    exercise_result = await session.execute(
        select(Exercise)
        .where(Exercise.id == exercise_id)
        .options(selectinload(Exercise.options))
    )
    exercise = exercise_result.scalar_one_or_none()
    if exercise is None:
        return None

    option = next((item for item in exercise.options if item.id == selected_option_id), None)
    if option is None:
        return None

    correct_option = next((item for item in exercise.options if item.is_correct), None)
    if correct_option is None:
        return None

    already_answered_result = await session.execute(
        select(UserAnswer).where(
            UserAnswer.user_id == user.id,
            UserAnswer.lesson_id == lesson_id,
            UserAnswer.exercise_id == exercise_id,
        )
    )
    already_answered = already_answered_result.scalar_one_or_none()
    if already_answered is not None:
        # Повторный клик по кнопке не должен повторно портить статистику.
        return AnswerResult(
            is_correct=already_answered.is_correct,
            correct_option_text=correct_option.option_text,
            short_explanation=exercise.short_explanation,
            full_explanation=exercise.full_explanation,
            example_text=exercise.example_text,
            interesting_fact=exercise.interesting_fact,
        )

    is_correct = option.is_correct
    session.add(
        UserAnswer(
            user_id=user.id,
            lesson_id=lesson_id,
            exercise_id=exercise_id,
            selected_option_id=selected_option_id,
            is_correct=is_correct,
        )
    )

    if is_correct:
        lesson.correct_answers += 1
        exercise.correct_count += 1
    else:
        lesson.wrong_answers += 1
        exercise.wrong_count += 1

    await upsert_topic_progress(session, user.id, exercise.topic_id, is_correct)
    await session.commit()

    return AnswerResult(
        is_correct=is_correct,
        correct_option_text=correct_option.option_text,
        short_explanation=exercise.short_explanation,
        full_explanation=exercise.full_explanation,
        example_text=exercise.example_text,
        interesting_fact=exercise.interesting_fact,
    )


async def upsert_topic_progress(session: AsyncSession, user_id: int, topic_id: int, is_correct: bool) -> None:
    result = await session.execute(
        select(UserTopicProgress).where(
            UserTopicProgress.user_id == user_id,
            UserTopicProgress.topic_id == topic_id,
        )
    )
    progress = result.scalar_one_or_none()

    if progress is None:
        progress = UserTopicProgress(user_id=user_id, topic_id=topic_id)
        session.add(progress)
        await session.flush()

    progress.total_answers += 1
    if is_correct:
        progress.correct_answers += 1
    else:
        progress.wrong_answers += 1
    progress.mastery_score = round((progress.correct_answers / progress.total_answers) * 100, 2)
    progress.last_practiced_at = datetime.utcnow()


async def get_lesson_summary(session: AsyncSession, lesson_id: int, user_id: int) -> Lesson | None:
    result = await session.execute(select(Lesson).where(Lesson.id == lesson_id, Lesson.user_id == user_id))
    return result.scalar_one_or_none()


async def save_rule(session: AsyncSession, user: User, exercise_id: int) -> SavedRule | None:
    exercise_result = await session.execute(select(Exercise).where(Exercise.id == exercise_id))
    exercise = exercise_result.scalar_one_or_none()
    if exercise is None:
        return None

    title = exercise.question[:120]
    rule_text = exercise.full_explanation or exercise.short_explanation
    saved = SavedRule(user_id=user.id, exercise_id=exercise.id, title=title, rule_text=rule_text)
    session.add(saved)
    await session.commit()
    await session.refresh(saved)
    return saved


async def get_progress_text(session: AsyncSession, user: User) -> str:
    total_result = await session.execute(select(func.count(UserAnswer.id)).where(UserAnswer.user_id == user.id))
    total = int(total_result.scalar() or 0)

    correct_result = await session.execute(
        select(func.count(UserAnswer.id)).where(UserAnswer.user_id == user.id, UserAnswer.is_correct.is_(True))
    )
    correct = int(correct_result.scalar() or 0)
    wrong = max(total - correct, 0)
    percent = round((correct / total) * 100, 1) if total else 0

    topics_result = await session.execute(
        select(Topic.title, UserTopicProgress.mastery_score, UserTopicProgress.total_answers)
        .join(UserTopicProgress, UserTopicProgress.topic_id == Topic.id)
        .where(UserTopicProgress.user_id == user.id)
        .order_by(UserTopicProgress.mastery_score.desc())
    )
    rows = topics_result.all()

    lines = [
        "<b>🏆 Ваш прогресс</b>",
        "",
        f"Всего ответов: <b>{total}</b>",
        f"Верных ответов: <b>{correct}</b>",
        f"Ошибок: <b>{wrong}</b>",
        f"Точность: <b>{percent}%</b>",
    ]

    if rows:
        lines.extend(["", "<b>Темы:</b>"])
        for title, mastery_score, total_answers in rows[:5]:
            lines.append(f"• {title}: {float(mastery_score):.1f}% / {total_answers} отв.")
    else:
        lines.extend(["", "Пока статистики мало. Пройдите первое занятие - и здесь появится картина."])

    return "\n".join(lines)


async def get_exercise_details(session: AsyncSession, exercise_id: int) -> Exercise | None:
    result = await session.execute(
        select(Exercise)
        .where(Exercise.id == exercise_id)
        .options(selectinload(Exercise.options))
    )
    return result.scalar_one_or_none()


async def cancel_lesson(session: AsyncSession, lesson_id: int, user_id: int) -> Lesson | None:
    result = await session.execute(select(Lesson).where(Lesson.id == lesson_id, Lesson.user_id == user_id))
    lesson = result.scalar_one_or_none()
    if lesson is None:
        return None
    if lesson.status == "started":
        lesson.status = "cancelled"
        lesson.completed_at = datetime.utcnow()
        await session.commit()
        await session.refresh(lesson)
    return lesson
