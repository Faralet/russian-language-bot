from __future__ import annotations

from datetime import date, datetime, time
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(AsyncAttrs, DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255))
    first_name: Mapped[Optional[str]] = mapped_column(String(255))
    last_name: Mapped[Optional[str]] = mapped_column(String(255))
    language_code: Mapped[Optional[str]] = mapped_column(String(20))
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="user")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    goal: Mapped[Optional[str]] = mapped_column(String(100))
    level: Mapped[Optional[str]] = mapped_column(String(100))
    age_group: Mapped[Optional[str]] = mapped_column(String(50))
    occupation: Mapped[Optional[str]] = mapped_column(String(255))
    is_premium: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    premium_until: Mapped[Optional[datetime]] = mapped_column(DateTime)
    daily_question_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="Europe/Moscow")
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notification_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(10, 0, 0))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    last_active_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    referred_by: Mapped[Optional[int]] = mapped_column(BigInteger)
    referral_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    current_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    longest_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    streak_freezes: Mapped[int] = mapped_column(Integer, nullable=False, default=2, server_default="2")
    last_activity_on: Mapped[Optional[date]] = mapped_column(Date)

    lessons: Mapped[list["Lesson"]] = relationship(back_populates="user")


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    parent_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("topics.id", ondelete="SET NULL"))
    slug: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    emoji: Mapped[Optional[str]] = mapped_column(String(20))
    level: Mapped[str] = mapped_column(String(50), nullable=False, default="basic")
    is_premium: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_exam_topic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    exercises: Mapped[list["Exercise"]] = relationship(back_populates="topic")


class Exercise(Base):
    __tablename__ = "exercises"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    topic_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    type: Mapped[str] = mapped_column(String(50), nullable=False, default="single_choice")
    level: Mapped[str] = mapped_column(String(50), nullable=False, default="basic")
    question: Mapped[str] = mapped_column(Text, nullable=False)
    short_explanation: Mapped[str] = mapped_column(Text, nullable=False)
    full_explanation: Mapped[Optional[str]] = mapped_column(Text)
    example_text: Mapped[Optional[str]] = mapped_column(Text)
    interesting_fact: Mapped[Optional[str]] = mapped_column(Text)
    exam_type: Mapped[str] = mapped_column(String(50), nullable=False, default="none")
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft", index=True)
    difficulty_score: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wrong_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    topic: Mapped["Topic"] = relationship(back_populates="exercises")
    options: Mapped[list["ExerciseOption"]] = relationship(back_populates="exercise", cascade="all, delete-orphan")


class ExerciseOption(Base):
    __tablename__ = "exercise_options"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    exercise_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("exercises.id", ondelete="CASCADE"), nullable=False, index=True)
    option_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    explanation: Mapped[Optional[str]] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    exercise: Mapped["Exercise"] = relationship(back_populates="options")


class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    topic_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("topics.id", ondelete="SET NULL"))
    lesson_type: Mapped[str] = mapped_column(String(50), nullable=False, default="daily")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="started", index=True)
    total_questions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_answers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wrong_answers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    user: Mapped["User"] = relationship(back_populates="lessons")
    lesson_exercises: Mapped[list["LessonExercise"]] = relationship(back_populates="lesson", cascade="all, delete-orphan")


class LessonExercise(Base):
    __tablename__ = "lesson_exercises"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lesson_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True)
    exercise_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("exercises.id", ondelete="CASCADE"), nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    shown_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    lesson: Mapped["Lesson"] = relationship(back_populates="lesson_exercises")
    exercise: Mapped["Exercise"] = relationship()


class UserAnswer(Base):
    __tablename__ = "user_answers"
    # Один вопрос в одном занятии — ровно один засчитанный ответ.
    # Ограничение в БД защищает статистику от гонки при двойном клике.
    __table_args__ = (UniqueConstraint("user_id", "lesson_id", "exercise_id", name="uq_user_lesson_exercise_answer"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    lesson_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True)
    exercise_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("exercises.id", ondelete="CASCADE"), nullable=False, index=True)
    selected_option_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("exercise_options.id", ondelete="SET NULL"))
    text_answer: Mapped[Optional[str]] = mapped_column(Text)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    answered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    response_time_seconds: Mapped[Optional[int]] = mapped_column(Integer)


class UserExerciseReview(Base):
    """Интервальное повторение ошибок: 1 - 3 - 7 дней.

    Запись появляется после неверного ответа. Каждый верный ответ на
    повторении продвигает stage; после прохождения всех интервалов
    запись удаляется - правило считается усвоенным.
    """

    __tablename__ = "user_exercise_reviews"
    __table_args__ = (UniqueConstraint("user_id", "exercise_id", name="uq_user_exercise_review"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    exercise_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("exercises.id", ondelete="CASCADE"), nullable=False, index=True)
    stage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_review_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    last_wrong_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class UserTopicProgress(Base):
    __tablename__ = "user_topic_progress"
    __table_args__ = (UniqueConstraint("user_id", "topic_id", name="uq_user_topic_progress"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    topic_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True)
    total_answers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_answers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wrong_answers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mastery_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    last_practiced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class SavedRule(Base):
    __tablename__ = "saved_rules"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    exercise_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("exercises.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    rule_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class UserAchievement(Base):
    """Разблокированные достижения пользователя (по одному коду на достижение)."""

    __tablename__ = "user_achievements"
    __table_args__ = (UniqueConstraint("user_id", "code", name="uq_user_achievement"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    unlocked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_code: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    subscription_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("subscriptions.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(255))
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="RUB")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending", index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSONB)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class AiDraft(Base):
    __tablename__ = "ai_drafts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    topic_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("topics.id", ondelete="SET NULL"))
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    generated_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    review_comment: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    notification_type: Mapped[str] = mapped_column(String(100), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending", index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class AdminLog(Base):
    __tablename__ = "admin_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    admin_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_type: Mapped[Optional[str]] = mapped_column(String(100))
    entity_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    details: Mapped[Optional[dict]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
