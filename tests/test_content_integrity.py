"""Smoke-тест целостности банка заданий и текстов (без БД, токена и aiogram).

Импортирует только чистые data-модули, поэтому запускается в любом окружении:
    pytest tests/test_content_integrity.py
    или: python tests/test_content_integrity.py
"""
from __future__ import annotations

from app.data.seed_content import EXERCISES, TOPICS
from app.data.seed_passages import PASSAGES

TOPIC_SLUGS = {t["slug"] for t in TOPICS}


def test_bank_not_empty():
    assert len(EXERCISES) > 700, f"банк слишком мал: {len(EXERCISES)}"


def test_every_exercise_valid():
    for i, ex in enumerate(EXERCISES):
        opts = ex.get("options", [])
        typ = ex.get("type", "single_choice")
        texts = [str(o["text"]).strip() for o in opts]
        n_correct = sum(1 for o in opts if o.get("is_correct"))
        assert ex.get("topic_slug") in TOPIC_SLUGS, f"#{i}: неизвестная тема {ex.get('topic_slug')}"
        assert str(ex.get("question", "")).strip(), f"#{i}: пустой вопрос"
        assert str(ex.get("short_explanation", "")).strip(), f"#{i}: пустой разбор"
        assert len(set(texts)) == len(texts), f"#{i}: повторяющиеся варианты {texts}"
        if typ == "text_input":
            assert n_correct >= 1, f"#{i}: text_input без верного ответа"
        else:
            assert len(opts) >= 2, f"#{i}: single_choice меньше 2 вариантов"
            assert n_correct == 1, f"#{i}: должен быть ровно один верный вариант, найдено {n_correct}"


def test_no_duplicate_exercises():
    seen = set()
    for i, ex in enumerate(EXERCISES):
        key = (ex["topic_slug"], ex["question"], tuple(sorted(str(o["text"]).strip() for o in ex["options"])))
        assert key not in seen, f"#{i}: дубль задания {key}"
        seen.add(key)


def test_passages_valid():
    assert len(PASSAGES) >= 5, f"мало текстов: {len(PASSAGES)}"
    for pi, p in enumerate(PASSAGES):
        assert str(p.get("text", "")).strip(), f"текст #{pi}: пустой"
        assert p.get("tasks"), f"текст #{pi}: нет заданий"
        for ti, task in enumerate(p["tasks"]):
            opts = task["options"]
            assert len(opts) >= 2, f"текст #{pi} задание #{ti}: меньше 2 вариантов"
            assert sum(1 for o in opts if o[1]) == 1, f"текст #{pi} задание #{ti}: не один верный"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK: {name}")
    print(f"\nВсего заданий: {len(EXERCISES)} | тем: {len(TOPICS)} | текстов: {len(PASSAGES)}")
