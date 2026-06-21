"""Целостность seed-контента: каждое упражнение обязано быть корректным."""
from __future__ import annotations

from app.data.seed_content import EXERCISES, TOPICS


def test_topics_have_unique_slugs() -> None:
    slugs = [topic["slug"] for topic in TOPICS]
    assert len(slugs) == len(set(slugs))


def test_every_exercise_references_existing_topic() -> None:
    slugs = {topic["slug"] for topic in TOPICS}
    for exercise in EXERCISES:
        assert exercise["topic_slug"] in slugs, exercise["question"]


def test_every_exercise_has_two_options_and_one_correct() -> None:
    for exercise in EXERCISES:
        options = exercise["options"]
        correct = sum(1 for option in options if option["is_correct"])
        if exercise.get("type") == "text_input":
            # Задание с вводом ответа: один или несколько ПРИНЯТЫХ ответов
            # (все варианты помечены is_correct=True).
            assert len(options) >= 1, exercise["question"]
            assert correct >= 1, exercise["question"]
        else:
            assert len(options) >= 2, exercise["question"]
            assert correct == 1, exercise["question"]


def test_every_exercise_has_question_and_explanation() -> None:
    for exercise in EXERCISES:
        assert exercise["question"].strip()
        assert exercise["short_explanation"].strip()


def test_no_empty_option_texts() -> None:
    for exercise in EXERCISES:
        for option in exercise["options"]:
            assert str(option["text"]).strip(), exercise["question"]


def test_no_duplicate_exercises() -> None:
    keys = set()
    for exercise in EXERCISES:
        key = (
            exercise["topic_slug"],
            exercise["question"],
            tuple(sorted(option["text"] for option in exercise["options"])),
        )
        assert key not in keys, f"Дубль: {key}"
        keys.add(key)


def test_exercise_count_is_at_least_declared() -> None:
    assert len(EXERCISES) >= 500
