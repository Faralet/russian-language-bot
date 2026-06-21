"""Валидация JSON-упражнений для админ-импорта."""
from __future__ import annotations

import pytest

from app.services.admin_service import (
    ContentValidationError,
    validate_exercise_payload,
    validate_options,
)


def valid_payload() -> dict:
    return {
        "topic_slug": "governing",
        "level": "basic",
        "question": "Как правильно?",
        "options": [
            {"text": "Согласно приказу", "is_correct": True},
            {"text": "Согласно приказа", "is_correct": False},
        ],
        "short_explanation": "После «согласно» нужен дательный падеж.",
    }


def test_valid_payload_passes() -> None:
    validate_exercise_payload(valid_payload())


def test_missing_required_field() -> None:
    payload = valid_payload()
    del payload["short_explanation"]
    with pytest.raises(ContentValidationError, match="short_explanation"):
        validate_exercise_payload(payload)


def test_empty_question_rejected() -> None:
    payload = valid_payload()
    payload["question"] = "   "
    with pytest.raises(ContentValidationError, match="question"):
        validate_exercise_payload(payload)


def test_less_than_two_options_rejected() -> None:
    payload = valid_payload()
    payload["options"] = [{"text": "Один", "is_correct": True}]
    with pytest.raises(ContentValidationError, match="options"):
        validate_exercise_payload(payload)


def test_zero_correct_options_rejected() -> None:
    payload = valid_payload()
    for option in payload["options"]:
        option["is_correct"] = False
    with pytest.raises(ContentValidationError, match="ровно один"):
        validate_exercise_payload(payload)


def test_two_correct_options_rejected() -> None:
    payload = valid_payload()
    for option in payload["options"]:
        option["is_correct"] = True
    with pytest.raises(ContentValidationError, match="ровно один"):
        validate_exercise_payload(payload)


def test_empty_option_text_rejected() -> None:
    payload = valid_payload()
    payload["options"][1]["text"] = "  "
    with pytest.raises(ContentValidationError, match="непустой text"):
        validate_exercise_payload(payload)


def test_duplicate_option_texts_rejected() -> None:
    with pytest.raises(ContentValidationError, match="повторяться"):
        validate_options(
            [
                {"text": "Одинаково", "is_correct": True},
                {"text": "Одинаково", "is_correct": False},
            ]
        )


def test_unknown_level_rejected() -> None:
    payload = valid_payload()
    payload["level"] = "super-puper"
    with pytest.raises(ContentValidationError, match="level"):
        validate_exercise_payload(payload)


def test_unknown_type_rejected() -> None:
    payload = valid_payload()
    payload["type"] = "multi_choice"
    with pytest.raises(ContentValidationError, match="type"):
        validate_exercise_payload(payload)


def test_bad_tags_rejected() -> None:
    payload = valid_payload()
    payload["tags"] = ["норм", ""]
    with pytest.raises(ContentValidationError, match="tags"):
        validate_exercise_payload(payload)
