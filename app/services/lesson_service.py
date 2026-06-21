from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.services.score_service import SCORE_DISCLAIMER, score_line
from app.db.models import (
    Exercise,
    ExerciseOption,
    Lesson,
    LessonExercise,
    SavedRule,
    Topic,
    User,
    UserAnswer,
    UserExerciseReview,
    UserTopicProgress,
)

# Интервальное повторение: ошибся - вопрос вернется через 1, 3 и 7 дней.
REVIEW_INTERVALS_DAYS = (1, 3, 7)
# Сколько вопросов занятия дня можно отдать под повторение.
REVIEWS_PER_LESSON = 2


@dataclass
class AnswerResult:
    is_correct: bool
    correct_option_text: str
    short_explanation: str
    full_explanation: str | None
    example_text: str | None
    interesting_fact: str | None


def is_active_premium(user: User) -> bool:
    """Beta: все пользователи получают полный доступ бесплатно.

    После включения платежей заменить на:
        if not user.is_premium:
            return False
        return user.premium_until is None or user.premium_until > datetime.utcnow()
    """
    return True  # BETA MODE: снять до включения платежей


def today_bounds_utc(tz_name: str | None = None) -> tuple[datetime, datetime]:
    """Границы "сегодня" в часовом поясе приложения, переведенные в наивный UTC.

    answered_at хранится как наивный UTC (func.now() в контейнере Postgres),
    поэтому лимит должен сбрасываться в полночь APP_TIMEZONE, а не в полночь UTC.
    """
    tz = ZoneInfo(tz_name or get_settings().app_timezone)
    now_local = datetime.now(tz)
    start_local = datetime.combine(now_local.date(), time.min, tzinfo=tz)
    start_utc = start_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return start_utc, start_utc + timedelta(days=1)


async def count_user_answers_today(session: AsyncSession, user_id: int) -> int:
    start, end = today_bounds_utc()
    result = await session.execute(
        select(func.count(UserAnswer.id)).where(
            UserAnswer.user_id == user_id,
            UserAnswer.answered_at >= start,
            UserAnswer.answered_at < end,
        )
    )
    return int(result.scalar() or 0)


def effective_daily_limit(user: User, now: datetime | None = None) -> int:
    """Дневной лимит с учетом велком-бонуса.

    Первые WELCOME_BONUS_DAYS дней после регистрации лимит удваивается:
    новичок должен успеть распробовать продукт, а не упереться в стену
    на пятом вопросе первого дня.
    """
    settings = get_settings()
    base = user.daily_question_limit or settings.free_daily_question_limit
    now = now or datetime.utcnow()
    created = user.created_at
    if created is None or (now - created) < timedelta(days=settings.welcome_bonus_days):
        return base * 2
    return base


async def can_start_lesson(session: AsyncSession, user: User) -> tuple[bool, int, int]:
    if is_active_premium(user):
        return True, 0, 999999
    used = await count_user_answers_today(session, user.id)
    limit = effective_daily_limit(user)
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


async def _pick_exercises(
    session: AsyncSession,
    user: User,
    count: int,
    topic_id: int | None = None,
    exclude_ids: set[int] | None = None,
    only_exam: bool = False,
) -> list[Exercise]:
    """Подбор упражнений: сначала те, на которые пользователь еще не отвечал.

    Если новых не хватает, добираем уже отвеченными (случайно), чтобы занятие
    всегда было полным. Так база не «заканчивается» внезапно для пользователя.
    """
    if count <= 0:
        return []
    exclude_ids = exclude_ids or set()

    answered_subq = select(UserAnswer.exercise_id).where(UserAnswer.user_id == user.id)

    filters = [Exercise.status == "published", Topic.status == "active"]
    if topic_id is not None:
        filters.append(Exercise.topic_id == topic_id)
    if only_exam:
        filters.append(Topic.slug == "exam")
    if not is_active_premium(user):
        filters.append(Topic.is_premium.is_(False))
    if exclude_ids:
        filters.append(Exercise.id.not_in(exclude_ids))

    fresh_result = await session.execute(
        select(Exercise)
        .join(Topic, Topic.id == Exercise.topic_id)
        .where(*filters, Exercise.id.not_in(answered_subq))
        .order_by(func.random())
        .limit(count)
    )
    picked = list(fresh_result.scalars().all())

    if len(picked) < count:
        # Новых не хватило - добираем уже отвеченными, без только что выбранных.
        picked_ids = exclude_ids | {ex.id for ex in picked}
        repeat_filters = [Exercise.status == "published", Topic.status == "active"]
        if topic_id is not None:
            repeat_filters.append(Exercise.topic_id == topic_id)
        if only_exam:
            repeat_filters.append(Topic.slug == "exam")
        if not is_active_premium(user):
            repeat_filters.append(Topic.is_premium.is_(False))
        if picked_ids:
            repeat_filters.append(Exercise.id.not_in(picked_ids))
        repeat_result = await session.execute(
            select(Exercise)
            .join(Topic, Topic.id == Exercise.topic_id)
            .where(*repeat_filters)
            .order_by(func.random())
            .limit(count - len(picked))
        )
        picked.extend(repeat_result.scalars().all())
    return picked


async def create_lesson(
    session: AsyncSession,
    user: User,
    lesson_type: str = "daily",
    topic_id: int | None = None,
    questions_count: int | None = None,
) -> Lesson | None:
    settings = get_settings()
    questions_count = questions_count or settings.lesson_questions_count

    exercises: list[Exercise] = []

    # Интервальное повторение: до REVIEWS_PER_LESSON вопросов занятия дня
    # отдаем правилам, которым пора вернуться (1-3-7 дней после ошибки).
    if topic_id is None and lesson_type == "daily":
        exercises.extend(
            await get_due_review_exercises(session, user.id, min(REVIEWS_PER_LESSON, questions_count))
        )

    # Персонализация: если цель - экзамен, ~40% вопросов из темы ЕГЭ/ОГЭ.
    if topic_id is None and user.goal in {"ege", "oge"} and len(exercises) < questions_count:
        exam_count = min(
            max(1, round(questions_count * 0.4)),
            questions_count - len(exercises),
        )
        exercises.extend(
            await _pick_exercises(
                session, user, exam_count, only_exam=True,
                exclude_ids={ex.id for ex in exercises},
            )
        )

    exercises.extend(
        await _pick_exercises(
            session,
            user,
            questions_count - len(exercises),
            topic_id=topic_id,
            exclude_ids={ex.id for ex in exercises},
        )
    )

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

    # Сначала - просроченные повторения (интервальный график 1-3-7).
    due = await get_due_review_exercises(session, user.id, questions_count)
    due_ids = {ex.id for ex in due}

    # Затем добираем недавними ошибками: находим ID упражнений с ошибками
    # (по последней ошибке), без дублей, и только потом ограничиваем количеством.
    wrong_ids_result = await session.execute(
        select(UserAnswer.exercise_id, func.max(UserAnswer.answered_at).label("last_wrong"))
        .join(Exercise, Exercise.id == UserAnswer.exercise_id)
        .where(
            UserAnswer.user_id == user.id,
            UserAnswer.is_correct.is_(False),
            Exercise.status == "published",
        )
        .group_by(UserAnswer.exercise_id)
        .order_by(func.max(UserAnswer.answered_at).desc())
        .limit(questions_count)
    )
    wrong_ids = [row[0] for row in wrong_ids_result.all() if row[0] not in due_ids]
    exercises: list[Exercise] = list(due)
    if wrong_ids and len(exercises) < questions_count:
        exercises_result = await session.execute(select(Exercise).where(Exercise.id.in_(wrong_ids)))
        by_id = {ex.id: ex for ex in exercises_result.scalars().all()}
        exercises.extend(by_id[i] for i in wrong_ids if i in by_id)
        exercises = exercises[:questions_count]

    if not exercises:
        # Ошибок и повторов нет вообще - предлагаем обычную случайную тренировку.
        return await create_lesson(session, user, lesson_type="random", questions_count=questions_count)

    if len(exercises) < questions_count:
        # Ошибок мало - добираем обычными вопросами, но повторы остаются первыми.
        exercises.extend(
            await _pick_exercises(
                session, user,
                questions_count - len(exercises),
                exclude_ids={ex.id for ex in exercises},
            )
        )

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


DIAGNOSTIC_TOPIC_ORDER = [
    "stress", "paronyms", "spelling", "punctuation", "speech_accuracy", "exam", "governing",
]
DIAGNOSTIC_SIZE = 7


async def create_diagnostic_lesson(session: AsyncSession, user: User) -> Lesson | None:
    """Стартовая диагностика: по одному заданию из разных тем, чтобы оценить уровень."""
    topics = (await session.execute(select(Topic).where(Topic.status == "active"))).scalars().all()
    slug_to_id = {t.slug: t.id for t in topics}

    exercises: list[Exercise] = []
    used_ids: set[int] = set()
    for slug in DIAGNOSTIC_TOPIC_ORDER:
        topic_id = slug_to_id.get(slug)
        if topic_id is None:
            continue
        filters = [Exercise.status == "published", Exercise.topic_id == topic_id]
        if used_ids:
            filters.append(Exercise.id.not_in(used_ids))
        ex = (await session.execute(
            select(Exercise).where(*filters).order_by(func.random()).limit(1)
        )).scalars().first()
        if ex is not None:
            exercises.append(ex)
            used_ids.add(ex.id)
        if len(exercises) >= DIAGNOSTIC_SIZE:
            break

    if len(exercises) < DIAGNOSTIC_SIZE:
        exercises.extend(await _pick_exercises(
            session, user, DIAGNOSTIC_SIZE - len(exercises), exclude_ids=used_ids,
        ))

    if not exercises:
        return None

    lesson = Lesson(
        user_id=user.id,
        lesson_type="diagnostic",
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


async def get_lesson_topic_breakdown(
    session: AsyncSession, lesson_id: int, user_id: int
) -> list[tuple[str, int, int]]:
    """Для занятия: список (тема, верных, всего) по ответам пользователя."""
    rows = (await session.execute(
        select(
            Topic.title,
            func.sum(case((UserAnswer.is_correct.is_(True), 1), else_=0)),
            func.count(UserAnswer.id),
        )
        .join(Exercise, Exercise.id == UserAnswer.exercise_id)
        .join(Topic, Topic.id == Exercise.topic_id)
        .where(UserAnswer.lesson_id == lesson_id, UserAnswer.user_id == user_id)
        .group_by(Topic.title)
    )).all()
    return [(title, int(correct or 0), int(total or 0)) for title, correct, total in rows]


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
    try:
        # Уникальный индекс может сработать не только на commit, но и на
        # autoflush внутри upsert_topic_progress, поэтому try охватывает
        # всю запись целиком.
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
        await update_review_schedule(session, user.id, exercise_id, is_correct)
        await session.commit()
    except IntegrityError:
        # Два очень быстрых клика могли одновременно пройти проверку выше.
        # Уникальный индекс в БД пропускает только один ответ; второй
        # откатываем и возвращаем уже засчитанный результат.
        await session.rollback()
        repeat_result = await session.execute(
            select(UserAnswer).where(
                UserAnswer.user_id == user.id,
                UserAnswer.lesson_id == lesson_id,
                UserAnswer.exercise_id == exercise_id,
            )
        )
        repeat = repeat_result.scalar_one_or_none()
        if repeat is None:
            return None
        return AnswerResult(
            is_correct=repeat.is_correct,
            correct_option_text=correct_option.option_text,
            short_explanation=exercise.short_explanation,
            full_explanation=exercise.full_explanation,
            example_text=exercise.example_text,
            interesting_fact=exercise.interesting_fact,
        )

    return AnswerResult(
        is_correct=is_correct,
        correct_option_text=correct_option.option_text,
        short_explanation=exercise.short_explanation,
        full_explanation=exercise.full_explanation,
        example_text=exercise.example_text,
        interesting_fact=exercise.interesting_fact,
    )


def _normalize_answer(text: str) -> str:
    """Нормализация введенного ответа: регистр, ё->е, лишние пробелы."""
    return " ".join((text or "").lower().replace("ё", "е").split())


async def answer_text_exercise(
    session: AsyncSession,
    user: User,
    lesson_id: int,
    exercise_id: int,
    text_answer: str,
) -> AnswerResult | None:
    """Засчитывает текстовый ответ (формат ЕГЭ «впиши слово»).

    Принятые ответы хранятся как варианты с is_correct=True. Сравнение -
    по нормализованному тексту. Запись идет в ту же статистику, прогресс по
    темам и график интервального повторения, что и обычный ответ.
    """
    lesson = (await session.execute(
        select(Lesson).where(Lesson.id == lesson_id, Lesson.user_id == user.id, Lesson.status == "started")
    )).scalar_one_or_none()
    if lesson is None:
        return None

    in_lesson = (await session.execute(
        select(LessonExercise.id).where(
            LessonExercise.lesson_id == lesson_id, LessonExercise.exercise_id == exercise_id
        )
    )).scalar_one_or_none()
    if in_lesson is None:
        return None

    exercise = (await session.execute(
        select(Exercise).where(Exercise.id == exercise_id).options(selectinload(Exercise.options))
    )).scalar_one_or_none()
    if exercise is None:
        return None

    accepted = [o for o in exercise.options if o.is_correct]
    correct_option = accepted[0] if accepted else None
    if correct_option is None:
        return None

    normalized = _normalize_answer(text_answer)
    is_correct = any(_normalize_answer(o.option_text) == normalized for o in accepted)

    already = (await session.execute(
        select(UserAnswer).where(
            UserAnswer.user_id == user.id,
            UserAnswer.lesson_id == lesson_id,
            UserAnswer.exercise_id == exercise_id,
        )
    )).scalar_one_or_none()
    if already is not None:
        return AnswerResult(
            is_correct=already.is_correct,
            correct_option_text=correct_option.option_text,
            short_explanation=exercise.short_explanation,
            full_explanation=exercise.full_explanation,
            example_text=exercise.example_text,
            interesting_fact=exercise.interesting_fact,
        )

    try:
        session.add(UserAnswer(
            user_id=user.id,
            lesson_id=lesson_id,
            exercise_id=exercise_id,
            selected_option_id=correct_option.id if is_correct else None,
            text_answer=(text_answer or "")[:500],
            is_correct=is_correct,
        ))
        if is_correct:
            lesson.correct_answers += 1
            exercise.correct_count += 1
        else:
            lesson.wrong_answers += 1
            exercise.wrong_count += 1
        await upsert_topic_progress(session, user.id, exercise.topic_id, is_correct)
        await update_review_schedule(session, user.id, exercise_id, is_correct)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        repeat = (await session.execute(
            select(UserAnswer).where(
                UserAnswer.user_id == user.id,
                UserAnswer.lesson_id == lesson_id,
                UserAnswer.exercise_id == exercise_id,
            )
        )).scalar_one_or_none()
        if repeat is None:
            return None
        is_correct = repeat.is_correct

    return AnswerResult(
        is_correct=is_correct,
        correct_option_text=correct_option.option_text,
        short_explanation=exercise.short_explanation,
        full_explanation=exercise.full_explanation,
        example_text=exercise.example_text,
        interesting_fact=exercise.interesting_fact,
    )


def next_review_interval(stage: int) -> timedelta | None:
    """Интервал до следующего повторения или None, если правило усвоено."""
    if stage >= len(REVIEW_INTERVALS_DAYS):
        return None
    return timedelta(days=REVIEW_INTERVALS_DAYS[stage])


async def update_review_schedule(
    session: AsyncSession,
    user_id: int,
    exercise_id: int,
    is_correct: bool,
) -> None:
    """Обновляет график повторения после ответа. Без commit - им управляет вызывающий."""
    now = datetime.utcnow()
    result = await session.execute(
        select(UserExerciseReview).where(
            UserExerciseReview.user_id == user_id,
            UserExerciseReview.exercise_id == exercise_id,
        )
    )
    review = result.scalar_one_or_none()

    if not is_correct:
        # Любая ошибка возвращает правило в начало цикла.
        if review is None:
            review = UserExerciseReview(user_id=user_id, exercise_id=exercise_id)
            session.add(review)
        review.stage = 0
        review.last_wrong_at = now
        review.next_review_at = now + next_review_interval(0)
        return

    if review is None:
        return  # верный ответ вне цикла повторения - ничего планировать не нужно

    review.stage += 1
    interval = next_review_interval(review.stage)
    if interval is None:
        # Все интервалы пройдены - правило усвоено.
        await session.delete(review)
    else:
        review.next_review_at = now + interval


async def get_due_review_exercises(session: AsyncSession, user_id: int, limit: int) -> list[Exercise]:
    """Упражнения, которым пора вернуться на повторение."""
    if limit <= 0:
        return []
    result = await session.execute(
        select(Exercise)
        .join(UserExerciseReview, UserExerciseReview.exercise_id == Exercise.id)
        .where(
            UserExerciseReview.user_id == user_id,
            UserExerciseReview.next_review_at <= datetime.utcnow(),
            Exercise.status == "published",
        )
        .order_by(UserExerciseReview.next_review_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


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


async def get_notifications_enabled(session: AsyncSession, user_id: int) -> bool:
    result = await session.execute(select(User.notifications_enabled).where(User.id == user_id))
    value = result.scalar_one_or_none()
    return bool(value) if value is not None else True


async def get_lesson_summary(session: AsyncSession, lesson_id: int, user_id: int) -> Lesson | None:
    result = await session.execute(select(Lesson).where(Lesson.id == lesson_id, Lesson.user_id == user_id))
    return result.scalar_one_or_none()


async def save_rule(session: AsyncSession, user: User, exercise_id: int) -> SavedRule | None:
    exercise_result = await session.execute(select(Exercise).where(Exercise.id == exercise_id))
    exercise = exercise_result.scalar_one_or_none()
    if exercise is None:
        return None

    # Повторный клик не должен плодить дубли в личной библиотеке.
    existing_result = await session.execute(
        select(SavedRule).where(SavedRule.user_id == user.id, SavedRule.exercise_id == exercise.id)
    )
    existing = existing_result.scalars().first()
    if existing is not None:
        return existing

    title = exercise.question[:120]
    rule_text = exercise.full_explanation or exercise.short_explanation
    saved = SavedRule(user_id=user.id, exercise_id=exercise.id, title=title, rule_text=rule_text)
    session.add(saved)
    await session.commit()
    await session.refresh(saved)
    return saved


async def get_saved_rules(session: AsyncSession, user: User, limit: int = 10) -> list[SavedRule]:
    result = await session.execute(
        select(SavedRule)
        .where(SavedRule.user_id == user.id)
        .order_by(SavedRule.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_streak_days(session: AsyncSession, user_id: int) -> int:
    """Сколько дней подряд (включая сегодня или вчера) пользователь отвечал.

    Даты считаются в часовом поясе приложения.
    """
    tz = ZoneInfo(get_settings().app_timezone)
    since = datetime.utcnow() - timedelta(days=60)
    result = await session.execute(
        select(UserAnswer.answered_at).where(UserAnswer.user_id == user_id, UserAnswer.answered_at >= since)
    )
    local_dates = {
        ts.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).date()
        for ts in result.scalars().all()
    }
    if not local_dates:
        return 0
    today = datetime.now(tz).date()
    # Серия не обнуляется, пока день еще не закончился: считаем от сегодня,
    # а если сегодня занятия не было — от вчера.
    cursor = today if today in local_dates else today - timedelta(days=1)
    streak = 0
    while cursor in local_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


async def get_overall_accuracy(session: AsyncSession, user_id: int) -> tuple[int, int]:
    """Возвращает (верных, всего) ответов пользователя за все время."""
    total = int((await session.execute(
        select(func.count(UserAnswer.id)).where(UserAnswer.user_id == user_id)
    )).scalar() or 0)
    correct = int((await session.execute(
        select(func.count(UserAnswer.id)).where(
            UserAnswer.user_id == user_id, UserAnswer.is_correct.is_(True)
        )
    )).scalar() or 0)
    return correct, total


async def get_progress_text(session: AsyncSession, user: User) -> str:
    total_result = await session.execute(select(func.count(UserAnswer.id)).where(UserAnswer.user_id == user.id))
    total = int(total_result.scalar() or 0)

    correct_result = await session.execute(
        select(func.count(UserAnswer.id)).where(UserAnswer.user_id == user.id, UserAnswer.is_correct.is_(True))
    )
    correct = int(correct_result.scalar() or 0)
    wrong = max(total - correct, 0)
    percent = round((correct / total) * 100, 1) if total else 0

    lessons_result = await session.execute(
        select(func.count(Lesson.id)).where(Lesson.user_id == user.id, Lesson.status == "completed")
    )
    lessons_completed = int(lessons_result.scalar() or 0)

    streak = user.current_streak or 0

    topics_result = await session.execute(
        select(Topic.title, UserTopicProgress.mastery_score, UserTopicProgress.total_answers)
        .join(UserTopicProgress, UserTopicProgress.topic_id == Topic.id)
        .where(UserTopicProgress.user_id == user.id)
        .order_by(UserTopicProgress.mastery_score.desc())
    )
    rows = topics_result.all()

    lines = ["<b>🏆 Твой прогресс</b>", ""]

    score = score_line(correct, total)
    if score:
        lines.extend([score, f"<i>{SCORE_DISCLAIMER}</i>", ""])

    from app.services.gamification_service import get_earned_achievements, level_info
    lvl = level_info(correct)
    level_line = f"🎚 Уровень {lvl['level']}: <b>{lvl['title']}</b>"
    if lvl["next_threshold"]:
        level_line += f" (до следующего: {lvl['to_next']} верных)"

    lines.extend([
        level_line,
        f"Занятий пройдено: <b>{lessons_completed}</b>",
        f"Ответов: <b>{total}</b> | верных: <b>{correct}</b> | ошибок: <b>{wrong}</b>",
        f"Точность: <b>{percent}%</b>",
    ])

    if streak >= 2:
        lines.append(f"🔥 Серия: <b>{streak}</b> дн. подряд")
        if streak >= 7:
            lines.append("<i>Неделя без пропусков. Так и набирается балл.</i>")
    elif streak == 1:
        lines.append("🌱 Серия: <b>1</b> день. Не теряй темп - завтра продолжим.")
    if (user.streak_freezes or 0) > 0:
        lines.append(f"🧊 Заморозки серии: <b>{user.streak_freezes}</b>")

    # Сильные и слабые темы считаем только там, где есть минимальная статистика.
    meaningful = [(t, float(m), n) for t, m, n in rows if n >= 3]
    if meaningful:
        strong = meaningful[0]
        weak = meaningful[-1]
        lines.extend(["", f"Сильная тема: <b>{strong[0]}</b> ({strong[1]:.0f}%)"])
        if weak[0] != strong[0]:
            lines.append(f"Стоит повторить: <b>{weak[0]}</b> ({weak[1]:.0f}%)")

    if rows:
        lines.extend(["", "<b>Темы:</b>"])
        for title, mastery_score, total_answers in rows[:6]:
            lines.append(f"• {title}: {float(mastery_score):.1f}% / {total_answers} отв.")
    else:
        lines.extend(["", "Пока статистики мало. Пройди первое занятие - и здесь появится картина."])

    earned = await get_earned_achievements(session, user)
    if earned:
        lines.extend(["", "<b>Достижения:</b> " + " ".join(a.emoji for a in earned)])

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


# Привязка тем к номерам заданий ЕГЭ (Phase 1) - для пути и прогноза балла.
EGE_TASK_BY_SLUG = {
    "stress": "№4",
    "paronyms": "№5",
    "speech_accuracy": "№6",
    "governing": "нормы",
    "syntax": "№8",
    "spelling": "№9-15",
    "punctuation": "№16-21",
    "exam": "ЕГЭ/ОГЭ",
}


async def get_path_overview(session: AsyncSession, user: User) -> list[tuple[Topic, float, int]]:
    """Юниты пути: список (тема, освоенность %, число ответов) в порядке пути."""
    topics = await get_active_topics(session, include_premium=is_active_premium(user))
    rows = (await session.execute(
        select(
            UserTopicProgress.topic_id,
            UserTopicProgress.mastery_score,
            UserTopicProgress.total_answers,
        ).where(UserTopicProgress.user_id == user.id)
    )).all()
    progress = {tid: (float(mastery or 0), int(total or 0)) for tid, mastery, total in rows}
    overview: list[tuple[Topic, float, int]] = []
    for topic in topics:
        mastery, total = progress.get(topic.id, (0.0, 0))
        overview.append((topic, mastery, total))
    return overview
