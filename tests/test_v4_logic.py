"""Unit-тесты чистой логики v4: баллы, серии, рефералка, прогноз, объем, нормализация.

Не требуют БД. Запуск:
    pytest tests/test_v4_logic.py
    или: python tests/test_v4_logic.py
"""
from __future__ import annotations

import os
from datetime import date

os.environ.setdefault("BOT_TOKEN", "123:TEST")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://t:t@localhost:5432/t")
os.environ.setdefault("ADMIN_TELEGRAM_IDS", "1")

from app.services.score_service import accuracy_to_test_score, score_line, PRIMARY_TO_TEST, MAX_PRIMARY
from app.services.streak_service import compute_streak
from app.services.referral_service import parse_ref_payload, referral_link, referral_payload, share_dialog_url
from app.services.exam_score_service import format_score_report
from app.services.essay_service import check_essay, MIN_WORDS as ESSAY_MIN
from app.services.oge_service import check_text, MIN_WORDS as OGE_MIN
from app.services.gamification_service import level_info, ACHIEVEMENTS, format_new_achievements
from app.services.lesson_service import _normalize_answer


def test_score_mapping_bounds_and_monotonic():
    assert len(PRIMARY_TO_TEST) == MAX_PRIMARY + 1
    assert accuracy_to_test_score(0.0) == 0
    assert accuracy_to_test_score(1.0) == 100
    assert accuracy_to_test_score(0.5) == PRIMARY_TO_TEST[25]
    prev = -1
    for p in PRIMARY_TO_TEST:
        assert p >= prev  # неубывающая шкала
        prev = p


def test_score_line_requires_min_answers():
    assert score_line(2, 4) is None        # меньше 5 ответов
    assert score_line(5, 5) is not None     # достаточно данных


def test_streak_increments_and_resets():
    today = date(2026, 6, 18)
    assert compute_streak(None, today, 0, 2).streak == 1                 # первый раз
    assert compute_streak(today, today, 7, 2).counted_today is False     # повтор за день
    assert compute_streak(date(2026, 6, 17), today, 6, 2).streak == 7    # вчера -> +1


def test_streak_freeze_bridges_one_gap():
    today = date(2026, 6, 18)
    with_freeze = compute_streak(date(2026, 6, 16), today, 6, 2)         # пропуск 1 дня, есть заморозка
    assert with_freeze.streak == 7 and with_freeze.froze and with_freeze.freezes == 1
    no_freeze = compute_streak(date(2026, 6, 16), today, 6, 0)           # пропуск, нет заморозки
    assert no_freeze.streak == 1 and no_freeze.reset
    big_gap = compute_streak(date(2026, 6, 10), today, 6, 2)             # пропуск 8 дней
    assert big_gap.streak == 1 and big_gap.reset


def test_streak_milestone():
    res = compute_streak(date(2026, 6, 17), date(2026, 6, 18), 6, 0)
    assert res.streak == 7 and res.milestone == 7


def test_referral_parse_and_link():
    assert parse_ref_payload("ref_42") == 42
    assert parse_ref_payload("ref_abc") is None
    assert parse_ref_payload("hello") is None
    assert parse_ref_payload(None) is None
    assert referral_payload(7) == "ref_7"
    assert referral_link("MyBot", 7) == "https://t.me/MyBot?start=ref_7"
    assert "t.me/share/url" in share_dialog_url("MyBot", 7)


def test_exam_score_report():
    empty = format_score_report([], 0.0, 0)
    assert "мало практики" in empty.lower()
    items = [
        {"slug": "stress", "label": "№4 · ударения", "points": 1, "mastery": 90.0, "answers": 10},
        {"slug": "spelling", "label": "№9-15 · орфография", "points": 7, "mastery": 40.0, "answers": 20},
    ]
    rep = format_score_report(items, 0.9 * 1 + 0.4 * 7, 8)
    assert "Прогноз балла" in rep and "орфография" in rep


def test_essay_and_oge_wordcount():
    short = check_essay("одно два три")
    assert "🔴" in short
    long = check_essay(" ".join(["слово"] * (ESSAY_MIN + 5)))
    assert "✅" in long
    assert "🔴" in check_text(" ".join(["x"] * (OGE_MIN - 10)))
    assert "✅" in check_text(" ".join(["x"] * (OGE_MIN + 5)))


def test_level_info():
    assert level_info(0)["level"] == 1
    assert level_info(20)["level"] == 2
    top = level_info(5000)
    assert top["level"] == 10 and top["next_threshold"] is None


def test_achievements_and_format():
    stats = {"total": 210, "correct": 180, "lessons": 40, "has_perfect": True,
             "accuracy": 85.7, "referrals": 5, "streak": 8}
    codes = [a.code for a in ACHIEVEMENTS if a.check(stats)]
    assert "first_lesson" in codes and "streak_7" in codes and "invite_5" in codes
    assert format_new_achievements([]) is None
    assert "достижение" in format_new_achievements([ACHIEVEMENTS[0]]).lower()


def test_normalize_answer():
    assert _normalize_answer("  ПоЕзжАй ") == "поезжай"
    assert _normalize_answer("жёлтый") == "желтый"
    assert _normalize_answer("в  течение") == "в течение"


if __name__ == "__main__":
    fns = sorted(n for n in dir() if n.startswith("test_"))
    for n in fns:
        globals()[n]()
        print("OK:", n)
    print(f"\nВсе {len(fns)} unit-тестов логики v4 прошли")
