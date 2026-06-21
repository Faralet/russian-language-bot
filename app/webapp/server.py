"""FastAPI-сервер для Telegram Mini App «Точный русский».

Работает рядом с ботом, использует ту же БД и ту же доменную логику.
Каждый запрос авторизуется по Telegram initData (см. auth.py).

Запуск (в контейнере):
    uvicorn app.webapp.server:app --host 127.0.0.1 --port 8081
"""
from __future__ import annotations

import re

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Exercise, ExerciseOption, Lesson, LessonExercise, Topic, User, UserAnswer
from app.db.session import async_session_factory
from app.webapp.auth import parse_and_validate
from app.services.lesson_service import answer_exercise, answer_text_exercise, today_bounds_utc
from app.services.streak_service import register_daily_activity_for

try:
    from app.services.gamification_service import level_info
except Exception:  # pragma: no cover - на случай рассинхронизации
    def level_info(correct_answers: int) -> dict:
        return {"level": 1, "next_threshold": None}

try:
    from app.services.score_service import accuracy_to_test_score
except Exception:  # pragma: no cover
    def accuracy_to_test_score(accuracy: float) -> int:
        return round(max(0.0, min(1.0, accuracy)) * 100)

try:
    from app.services.exam_score_service import task_readiness
except Exception:  # pragma: no cover
    task_readiness = None

settings = get_settings()

app = FastAPI(title="Точный русский — Mini App API", docs_url=None, redoc_url=None)

# Фронт и API раздаются с одного домена через Caddy, но на время отладки
# и для запуска фронта с другого origin разрешаем кросс-доступ.
# Авторизация идёт через заголовок initData, cookie/креды не используются.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower().replace("ё", "е"))


async def get_tg_user(
    authorization: str | None = Header(default=None),
    x_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
) -> dict:
    """Достаёт и валидирует initData. Формат заголовка: `Authorization: tma <initData>`."""
    init_data = None
    if authorization and authorization.lower().startswith("tma "):
        init_data = authorization[4:]
    elif x_init_data:
        init_data = x_init_data
    data = parse_and_validate(init_data or "", settings.bot_token)
    if not data or not data.get("user") or not data["user"].get("id"):
        raise HTTPException(status_code=401, detail="Невалидные данные авторизации Telegram")
    return data["user"]


async def _get_or_create_user(session: AsyncSession, tg: dict) -> User:
    tid = int(tg["id"])
    user = (
        await session.execute(select(User).where(User.telegram_id == tid))
    ).scalar_one_or_none()
    if user is None:
        user = User(
            telegram_id=tid,
            username=tg.get("username"),
            first_name=tg.get("first_name"),
            last_name=tg.get("last_name"),
            language_code=tg.get("language_code"),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/api/me")
async def me(tg: dict = Depends(get_tg_user)) -> dict:
    async with async_session_factory() as session:
        user = await _get_or_create_user(session, tg)
        total = (
            await session.execute(
                select(func.count()).select_from(UserAnswer).where(UserAnswer.user_id == user.id)
            )
        ).scalar_one()
        correct = (
            await session.execute(
                select(func.count())
                .select_from(UserAnswer)
                .where(UserAnswer.user_id == user.id, UserAnswer.is_correct.is_(True))
            )
        ).scalar_one()
        _ds, _de = today_bounds_utc()
        answered_today = (
            await session.execute(
                select(func.count())
                .select_from(UserAnswer)
                .where(
                    UserAnswer.user_id == user.id,
                    UserAnswer.answered_at >= _ds,
                    UserAnswer.answered_at < _de,
                )
            )
        ).scalar_one()

        try:
            info = level_info(int(correct))
            level = info.get("level", 1)
            level_title = info.get("title") or info.get("name")
            next_threshold = info.get("next_threshold")
        except Exception:
            level, level_title, next_threshold = 1, None, None

        accuracy = (correct / total) if total else 0.0
        return {
            "first_name": user.first_name or "друг",
            "level": level,
            "level_title": level_title,
            "xp": int(correct),
            "next_threshold": next_threshold,
            "streak": user.current_streak,
            "longest_streak": user.longest_streak,
            "answered_today": int(answered_today),
            "daily_goal": settings.daily_goal_questions,
            "total": int(total),
            "correct": int(correct),
            "accuracy": round(accuracy * 100),
            "projected_score": accuracy_to_test_score(accuracy) if total >= 5 else None,
        }


@app.get("/api/lesson")
async def lesson(topic: str | None = None, tg: dict = Depends(get_tg_user)) -> dict:
    count = max(1, settings.lesson_questions_count * settings.lesson_stages)
    async with async_session_factory() as session:
        user = await _get_or_create_user(session, tg)
        base_q = select(Exercise).where(Exercise.status == "published")
        scoped_q = base_q
        if topic:
            scoped_q = base_q.join(Topic, Topic.id == Exercise.topic_id).where(Topic.slug == topic)
        exercises = (
            await session.execute(scoped_q.order_by(func.random()).limit(count))
        ).scalars().all()
        if not exercises and topic:
            # В выбранной теме нет заданий — не оставляем пользователя с пустым уроком.
            exercises = (
                await session.execute(base_q.order_by(func.random()).limit(count))
            ).scalars().all()
        if not exercises:
            raise HTTPException(status_code=404, detail="Нет опубликованных заданий")

        new_lesson = Lesson(
            user_id=user.id,
            lesson_type="miniapp",
            status="started",
            total_questions=len(exercises),
        )
        session.add(new_lesson)
        await session.flush()

        questions: list[dict] = []
        for order, exercise in enumerate(exercises):
            session.add(
                LessonExercise(lesson_id=new_lesson.id, exercise_id=exercise.id, sort_order=order)
            )
            options = (
                await session.execute(
                    select(ExerciseOption)
                    .where(ExerciseOption.exercise_id == exercise.id)
                    .order_by(ExerciseOption.sort_order, ExerciseOption.id)
                )
            ).scalars().all()
            questions.append(
                {
                    "exercise_id": exercise.id,
                    "type": exercise.type,
                    "question": exercise.question,
                    # Правильность НЕ раскрываем: проверка только на сервере в /api/answer.
                    "options": (
                        []
                        if exercise.type == "text_input"
                        else [{"id": o.id, "text": o.option_text} for o in options]
                    ),
                }
            )
        await session.commit()
        return {
            "lesson_id": new_lesson.id,
            "stages": settings.lesson_stages,
            "per_stage": settings.lesson_questions_count,
            "questions": questions,
        }


class AnswerIn(BaseModel):
    lesson_id: int
    exercise_id: int
    option_id: int | None = None
    text: str | None = None


@app.post("/api/answer")
async def answer(body: AnswerIn, tg: dict = Depends(get_tg_user)) -> dict:
    async with async_session_factory() as session:
        user = await _get_or_create_user(session, tg)
        exercise = (
            await session.execute(select(Exercise).where(Exercise.id == body.exercise_id))
        ).scalar_one_or_none()
        if exercise is None:
            raise HTTPException(status_code=404, detail="Задание не найдено")

        # Канонический путь записи (как в боте): прогресс по темам, SRS,
        # итоги урока и счетчики задания обновляются здесь же.
        if exercise.type == "text_input":
            result = await answer_text_exercise(session, user, body.lesson_id, exercise.id, body.text or "")
        else:
            result = await answer_exercise(session, user, body.lesson_id, exercise.id, body.option_id or 0)

        # Серия дня (идемпотентно: засчитывается один раз в сутки).
        try:
            await register_daily_activity_for(session, user.id)
        except Exception:  # noqa: BLE001
            pass

        options = (
            await session.execute(
                select(ExerciseOption).where(ExerciseOption.exercise_id == exercise.id)
            )
        ).scalars().all()
        correct_ids = [o.id for o in options if o.is_correct]
        correct_texts = (
            [o.option_text for o in options if o.is_correct]
            if exercise.type == "text_input"
            else []
        )

        if result is None:
            # Урок не найден или не активен (например, устаревший lesson_id):
            # не блокируем пользователя, считаем правильность напрямую, без записи прогресса.
            if exercise.type == "text_input":
                accepted = {_norm(o.option_text) for o in options if o.is_correct}
                is_correct = _norm(body.text or "") in accepted and bool(accepted)
            else:
                is_correct = body.option_id in correct_ids
            return {
                "correct": is_correct,
                "correct_option_ids": correct_ids,
                "correct_texts": correct_texts,
                "explanation": exercise.short_explanation,
                "full_explanation": exercise.full_explanation,
            }

        return {
            "correct": result.is_correct,
            "correct_option_ids": correct_ids,
            "correct_texts": correct_texts,
            "explanation": result.short_explanation,
            "full_explanation": result.full_explanation,
        }


@app.get("/api/score")
async def score(tg: dict = Depends(get_tg_user)) -> dict:
    async with async_session_factory() as session:
        user = await _get_or_create_user(session, tg)
        # Большое число "из 100" считаем тем же ориентиром, что и на главном
        # экране (по общей точности ответов): иначе экраны расходятся, а число
        # не растет. Раньше сюда ошибочно попадали первичные баллы (0..28),
        # из-за чего показывалось "1".
        total = (await session.execute(
            select(func.count()).select_from(UserAnswer).where(UserAnswer.user_id == user.id)
        )).scalar_one()
        correct = (await session.execute(
            select(func.count()).select_from(UserAnswer).where(
                UserAnswer.user_id == user.id, UserAnswer.is_correct.is_(True)
            )
        )).scalar_one()
        accuracy = (correct / total) if total else 0.0
        projected = accuracy_to_test_score(accuracy) if total >= 5 else 0
        items_out: list[dict] = []
        if task_readiness is not None:
            try:
                raw_items, _primary, _covered = await task_readiness(session, user)
                items_out = [
                    {
                        "label": it.get("label"),
                        "mastery": round(float(it.get("mastery", 0) or 0)),
                        "answers": int(it.get("answers", 0) or 0),
                    }
                    for it in raw_items
                ]
            except Exception:
                items_out = []
        return {
            "projected": int(projected),
            "covered": int(total),
            "items": items_out,
        }
