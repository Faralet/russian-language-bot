"""End-to-end проверка бота на реальном PostgreSQL без Telegram.

Поднимает локальный PostgreSQL (pip install pgserver), прогоняет сид,
занятия, ответы, лимиты, гонку двойного клика и админку - 33 проверки.

Запуск из корня проекта:
    pip install pgserver
    python scripts/e2e_smoke.py
"""
import asyncio, os, sys
import pgserver

pg = pgserver.get_server("/tmp/pgdata")
uri = pg.get_uri()  # postgresql://postgres:...@/dbname?host=/tmp/...
print("PG URI:", uri)
os.environ["DATABASE_URL"] = uri.replace("postgresql://", "postgresql+asyncpg://")
os.environ["BOT_TOKEN"] = "123456:TEST"
os.environ["ADMIN_TELEGRAM_IDS"] = "111"
os.environ["APP_TIMEZONE"] = "Europe/Moscow"
sys.path.insert(0, ".")

from aiogram.types import User as TgUser
from sqlalchemy import func, select, text

PASS = []
def ok(name, cond=True):
    assert cond, f"FAIL: {name}"
    PASS.append(name); print(f"  ✓ {name}")

async def main():
    from app.db.init_db import init_database
    from app.db.session import async_session_factory, engine
    from app.db.models import Exercise, ExerciseOption, Topic, UserAnswer, Lesson
    from app.services.user_service import get_or_create_user
    from app.services import lesson_service as ls
    from app.services import admin_service as adm

    print("== init / seed ==")
    await init_database()
    async with async_session_factory() as s:
        topics = (await s.execute(select(func.count(Topic.id)))).scalar()
        exercises = (await s.execute(select(func.count(Exercise.id)))).scalar()
        idx = (await s.execute(text("select count(*) from pg_indexes where indexname='uq_user_lesson_exercise_answer'"))).scalar()
    ok("9 тем", topics == 9)
    ok("500 упражнений", exercises == 500)
    ok("уникальный индекс user_answers создан", idx == 1)

    await init_database()  # идемпотентность
    async with async_session_factory() as s:
        exercises2 = (await s.execute(select(func.count(Exercise.id)))).scalar()
    ok("повторный init не плодит дубли (500)", exercises2 == 500)

    print("== пользователь / занятие ==")
    tg_admin = TgUser(id=111, is_bot=False, first_name="Max")
    tg_user = TgUser(id=222, is_bot=False, first_name="Anna")
    from datetime import timedelta as _td, datetime as _dt
    async with async_session_factory() as s:
        admin = await get_or_create_user(s, tg_admin)
        user = await get_or_create_user(s, tg_user)
        ok("роль admin по ADMIN_TELEGRAM_IDS", admin.role == "admin")
        ok("обычный пользователь user", user.role == "user")

        # Свежий пользователь получает велком-бонус: двойной лимит.
        allowed, used, limit = await ls.can_start_lesson(s, user)
        ok("велком-бонус: лимит новичка 10", allowed and used == 0 and limit == 10)

        # Для проверки обычного лимита "состарим" пользователя.
        user.created_at = _dt.utcnow() - _td(days=10)
        admin.created_at = _dt.utcnow() - _td(days=10)
        await s.commit()
        await s.refresh(user)

        allowed, used, limit = await ls.can_start_lesson(s, user)
        ok("лимит на старте: 0/5, можно", allowed and used == 0 and limit == 5)

        lesson = await ls.create_lesson(s, user, lesson_type="daily", questions_count=5)
        ok("занятие создано на 5 вопросов", lesson is not None and lesson.total_questions == 5)

        # проходим занятие: 1-й вопрос отвечаем неверно, остальные верно
        wrong_done = 0
        while True:
            nxt = await ls.get_next_exercise(s, lesson.id, user.id)
            if nxt is None:
                break
            _, ex = nxt
            opts = sorted(ex.options, key=lambda o: o.sort_order)
            target = next(o for o in opts if not o.is_correct) if wrong_done == 0 else next(o for o in opts if o.is_correct)
            res = await ls.answer_exercise(s, user, lesson.id, ex.id, target.id)
            assert res is not None
            if wrong_done == 0:
                ok("неверный ответ показывает правильный вариант", not res.is_correct and res.correct_option_text)
                # повторный клик тем же ответом
                res2 = await ls.answer_exercise(s, user, lesson.id, ex.id, target.id)
                ok("повторный клик не падает и возвращает тот же вердикт", res2 is not None and res2.is_correct == res.is_correct)
            wrong_done += 1

        fresh = await ls.get_lesson_summary(s, lesson.id, user.id)
        ok("занятие завершено", fresh.status == "completed")
        ok("счет 4 из 5, повторный клик не задвоил", fresh.correct_answers == 4 and fresh.wrong_answers == 1)

        answers = (await s.execute(select(func.count(UserAnswer.id)).where(UserAnswer.user_id == user.id))).scalar()
        ok("в базе ровно 5 ответов", answers == 5)

        allowed, used, limit = await ls.can_start_lesson(s, user)
        ok("дневной лимит исчерпан: 5/5, нельзя", (not allowed) and used == 5)

        progress = await ls.get_progress_text(s, user)
        ok("прогресс: занятия/точность/серия", "Занятий пройдено: <b>1</b>" in progress and "80" in progress and "Серия" in progress)

        # премиум вручную без даты
        user.is_premium = True
        await s.commit()
        allowed, *_ = await ls.can_start_lesson(s, user)
        ok("ручной premium без даты снимает лимит", allowed)
        user.is_premium = False
        await s.commit()

    print("== гонка двойного клика ==")
    async with async_session_factory() as s:
        admin_u = await get_or_create_user(s, tg_admin)
        lesson2 = await ls.create_lesson(s, admin_u, lesson_type="daily", questions_count=3)
        nxt = await ls.get_next_exercise(s, lesson2.id, admin_u.id)
        _, ex2 = nxt
        correct_opt = next(o for o in ex2.options if o.is_correct)
        ex2_id, opt_id, lesson2_id, admin_id = ex2.id, correct_opt.id, lesson2.id, admin_u.id

    async def click():
        async with async_session_factory() as s2:
            u = await get_or_create_user(s2, tg_admin)
            return await ls.answer_exercise(s2, u, lesson2_id, ex2_id, opt_id)

    r1, r2 = await asyncio.gather(click(), click())
    ok("оба клика получили ответ (без исключений)", r1 is not None and r2 is not None)
    async with async_session_factory() as s:
        cnt = (await s.execute(select(func.count(UserAnswer.id)).where(
            UserAnswer.lesson_id == lesson2_id, UserAnswer.exercise_id == ex2_id))).scalar()
        lsn = (await s.execute(select(Lesson).where(Lesson.id == lesson2_id))).scalar_one()
        ok("засчитан ровно один ответ", cnt == 1)
        ok("счетчики занятия не задвоены", lsn.correct_answers + lsn.wrong_answers == 1)

    print("== тренировка ошибок ==")
    async with async_session_factory() as s:
        u = await get_or_create_user(s, tg_user)
        # добавим еще ошибок, чтобы было >=3
        l3 = await ls.create_lesson(s, u, lesson_type="daily", questions_count=4)
        while True:
            nxt = await ls.get_next_exercise(s, l3.id, u.id)
            if nxt is None: break
            _, ex = nxt
            wrong = next(o for o in ex.options if not o.is_correct)
            await ls.answer_exercise(s, u, l3.id, ex.id, wrong.id)
        ml = await ls.create_mistakes_lesson(s, u, questions_count=5)
        ok("занятие из ошибок собрано", ml is not None and ml.lesson_type == "mistakes" and ml.total_questions >= 3)

    print("== сохраненные правила ==")
    async with async_session_factory() as s:
        u = await get_or_create_user(s, tg_user)
        ex_id = (await s.execute(select(Exercise.id).limit(1))).scalar()
        r1 = await ls.save_rule(s, u, ex_id)
        r2 = await ls.save_rule(s, u, ex_id)
        ok("правило сохранено, дубль не создан", r1 is not None and r1.id == r2.id)
        rules = await ls.get_saved_rules(s, u)
        ok("список сохраненных правил отдается", len(rules) == 1)

    print("== админка ==")
    payload = {
        "topic_slug": "governing", "level": "basic", "question": "ТЕСТ: как правильно?",
        "options": [{"text": "Вариант А", "is_correct": True}, {"text": "Вариант Б", "is_correct": False}],
        "short_explanation": "Тестовое объяснение.", "tags": ["тест"], "status": "published",
    }
    async with async_session_factory() as s:
        new_ex = await adm.create_exercise_from_payload(s, payload, admin_user_id=1)
        ok("упражнение добавлено из JSON", new_ex.id == 501)
        try:
            bad = dict(payload); bad["options"] = [{"text": "Один", "is_correct": True}]
            await adm.create_exercise_from_payload(s, bad)
            ok("невалидный JSON отклонен", False)
        except adm.ContentValidationError:
            ok("невалидный JSON отклонен с понятной ошибкой")
        upd = await adm.update_exercise_from_payload(s, new_ex.id, {"short_explanation": "Новое объяснение.", "status": "draft"})
        ok("JSON-патч применен", upd.short_explanation == "Новое объяснение." and upd.status == "draft")
        await adm.set_exercise_status(s, new_ex.id, "published")
        txt = await adm.get_exercise_text(s, new_ex.id)
        ok("/exercise показывает карточку", "ТЕСТ: как правильно?" in txt and "✅" in txt)
        found = await adm.search_exercises_text(s, "согласно")
        ok("/search_exercises находит", "#" in found and "согласно" in found.lower())
        js, cnt = await adm.export_exercises_json(s)
        ok("экспорт JSON: 501 упражнение", cnt == 501 and '"topic_slug"' in js)

        # мягкое удаление: история пользователя не должна пострадать
        before = (await s.execute(select(func.count(UserAnswer.id)))).scalar()
        await adm.delete_exercise_by_id(s, new_ex.id, admin_user_id=1)
        deleted_ex = (await s.execute(select(Exercise).where(Exercise.id == new_ex.id))).scalar_one()
        after = (await s.execute(select(func.count(UserAnswer.id)))).scalar()
        opts_alive = (await s.execute(select(func.count(ExerciseOption.id)).where(ExerciseOption.exercise_id == new_ex.id))).scalar()
        ok("мягкое удаление: status=deleted, ответы и варианты целы", deleted_ex.status == "deleted" and before == after and opts_alive == 2)
        stats = await adm.get_admin_stats(s)
        ok("статистика админки строится", "Пользователей" in stats)

    print("== тексты бота (хендлеры /start и занятие) ==")
    class FakeMsg:
        def __init__(self, tg): self.from_user = tg; self.sent = []
        async def answer(self, text_, **kw): self.sent.append(text_)
    from app.bot.handlers.start import cmd_start
    from app.bot.handlers.lessons import start_lesson_for_user
    async with async_session_factory() as s:
        m = FakeMsg(TgUser(id=333, is_bot=False, first_name="New"))
        await cmd_start(m, s)
        ok("/start отвечает приветствием и меню", any("Добро пожаловать" in t for t in m.sent) and len(m.sent) == 2)
        m2 = FakeMsg(TgUser(id=333, is_bot=False, first_name="New"))
        await start_lesson_for_user(m2, s, m2.from_user, lesson_type="daily")
        ok("занятие дня стартует и показывает вопрос 1 из 5", any("Вопрос 1 из 5" in t for t in m2.sent))
        # пользователь с исчерпанным лимитом получает мягкий отказ
        m3 = FakeMsg(tg_user)
        await start_lesson_for_user(m3, s, tg_user, lesson_type="daily")
        ok("при исчерпанном лимите - мягкий отказ с премиум-витриной", any("разминка закончилась" in t for t in m3.sent))

    print("== v3.1: умный подбор ==")
    from datetime import datetime as dt, time as dtime
    async with async_session_factory() as s:
        smart = TgUser(id=444, is_bot=False, first_name="Smart")
        su = await get_or_create_user(s, smart)
        l1 = await ls.create_lesson(s, su, lesson_type="daily", questions_count=5)
        first_ids = set()
        while True:
            nxt = await ls.get_next_exercise(s, l1.id, su.id)
            if nxt is None: break
            _, ex = nxt
            first_ids.add(ex.id)
            await ls.answer_exercise(s, su, l1.id, ex.id, next(o for o in ex.options if o.is_correct).id)
        l2 = await ls.create_lesson(s, su, lesson_type="daily", questions_count=5)
        rows = await s.execute(select(Exercise.id).join(
            __import__("app.db.models", fromlist=["LessonExercise"]).LessonExercise,
            __import__("app.db.models", fromlist=["LessonExercise"]).LessonExercise.exercise_id == Exercise.id
        ).where(__import__("app.db.models", fromlist=["LessonExercise"]).LessonExercise.lesson_id == l2.id))
        second_ids = set(rows.scalars().all())
        ok("второе занятие без повторов первого", first_ids.isdisjoint(second_ids))

        # цель ЕГЭ -> в занятии есть экзаменационные вопросы
        from app.services.user_service import set_goal
        eger = await get_or_create_user(s, TgUser(id=555, is_bot=False, first_name="Ege"))
        await set_goal(s, eger, "ege")
        l3 = await ls.create_lesson(s, eger, lesson_type="daily", questions_count=5)
        rows = await s.execute(select(Topic.slug).join(Exercise, Exercise.topic_id == Topic.id).join(
            __import__("app.db.models", fromlist=["LessonExercise"]).LessonExercise,
            __import__("app.db.models", fromlist=["LessonExercise"]).LessonExercise.exercise_id == Exercise.id
        ).where(__import__("app.db.models", fromlist=["LessonExercise"]).LessonExercise.lesson_id == l3.id))
        slugs = list(rows.scalars().all())
        ok("цель ЕГЭ дает экзаменационные вопросы", slugs.count("exam") >= 2)

    print("== v3.1: напоминания ==")
    from app.services import notification_service as ns
    from zoneinfo import ZoneInfo

    class FakeBot:
        def __init__(self): self.sent = []
        async def send_message(self, chat_id, text, **kw): self.sent.append((chat_id, text))

    async with async_session_factory() as s:
        su = await get_or_create_user(s, TgUser(id=444, is_bot=False, first_name="Smart"))
        now_msk = dt.now(ZoneInfo("Europe/Moscow"))
        su.notification_time = dtime(now_msk.hour, now_msk.minute)
        su.notifications_enabled = True
        # остальным выключим, чтобы не шумели
        from app.db.models import User as DbU
        for u in (await s.execute(select(DbU).where(DbU.id != su.id))).scalars():
            u.notifications_enabled = False
        await s.commit()

    fake = FakeBot()
    sent1 = await ns.send_due_reminders(fake)
    ok("напоминание отправлено в нужную минуту", sent1 == 1 and len(fake.sent) == 1 and fake.sent[0][0] == 444)
    _rt = fake.sent[0][1].lower()
    ok("текст напоминания осмысленный", any(k in _rt for k in ("занятие", "разминка", "размяться", "грамотн")) and len(_rt) > 20)
    sent2 = await ns.send_due_reminders(fake)
    ok("повторное напоминание в тот же день не уходит", sent2 == 0)

    from app.services.user_service import set_notifications_enabled
    async with async_session_factory() as s:
        su = await get_or_create_user(s, TgUser(id=444, is_bot=False, first_name="Smart"))
        await set_notifications_enabled(s, su, False)
    sent3 = await ns.send_due_reminders(fake)
    ok("после отписки напоминания не уходят", sent3 == 0)

    print("== v3.1: финал занятия и буквы ==")
    async with async_session_factory() as s:
        nu = await get_or_create_user(s, TgUser(id=666, is_bot=False, first_name="Fin"))
        m = FakeMsg(TgUser(id=666, is_bot=False, first_name="Fin"))
        await start_lesson_for_user(m, s, m.from_user, lesson_type="daily")
        lid = None
        # пройдем занятие через сервис
        from app.db.models import Lesson as L
        lid = (await s.execute(select(L.id).where(L.user_id == nu.id).order_by(L.id.desc()))).scalars().first()
        while True:
            nxt = await ls.get_next_exercise(s, lid, nu.id)
            if nxt is None: break
            _, ex = nxt
            await ls.answer_exercise(s, nu, lid, ex.id, next(o for o in ex.options if o.is_correct).id)
        from app.bot.handlers.lessons import send_next_question
        m2 = FakeMsg(m.from_user)
        await send_next_question(m2, s, nu.id, lid)
        final = m2.sent[-1]
        ok("финал: точность и серия", "Точность" in final and ("Серия" in final or "серии" in final))

    print("== v3.3: интервальное повторение ==")
    from app.db.models import UserExerciseReview
    async with async_session_factory() as s:
        ru = await get_or_create_user(s, TgUser(id=777, is_bot=False, first_name="Rev"))
        rl = await ls.create_lesson(s, ru, lesson_type="daily", questions_count=2)
        nxt = await ls.get_next_exercise(s, rl.id, ru.id)
        _, rex = nxt
        wrong_opt = next(o for o in rex.options if not o.is_correct)
        await ls.answer_exercise(s, ru, rl.id, rex.id, wrong_opt.id)
        review = (await s.execute(select(UserExerciseReview).where(
            UserExerciseReview.user_id == ru.id, UserExerciseReview.exercise_id == rex.id))).scalar_one()
        delta = review.next_review_at - dt.utcnow()
        ok("ошибка планирует повтор через ~1 день", review.stage == 0 and 0.9 < delta.total_seconds()/86400 < 1.1)

        # делаем повтор просроченным - занятие дня должно подхватить его первым
        review.next_review_at = dt.utcnow() - __import__("datetime").timedelta(hours=1)
        await s.commit()
        rl2 = await ls.create_lesson(s, ru, lesson_type="daily", questions_count=5)
        nxt2 = await ls.get_next_exercise(s, rl2.id, ru.id)
        _, first_ex = nxt2
        ok("просроченный повтор - первым вопросом занятия", first_ex.id == rex.id)

        # верный ответ продвигает стадию: 0 -> 1, интервал ~3 дня
        corr = next(o for o in first_ex.options if o.is_correct)
        await ls.answer_exercise(s, ru, rl2.id, first_ex.id, corr.id)
        review = (await s.execute(select(UserExerciseReview).where(
            UserExerciseReview.user_id == ru.id, UserExerciseReview.exercise_id == rex.id))).scalar_one()
        delta = review.next_review_at - dt.utcnow()
        ok("верный ответ: stage 1, повтор через ~3 дня", review.stage == 1 and 2.9 < delta.total_seconds()/86400 < 3.1)

        # проходим оставшиеся стадии до усвоения
        for expected_days in (7,):
            review.next_review_at = dt.utcnow() - __import__("datetime").timedelta(hours=1)
            await s.commit()
            ml = await ls.create_mistakes_lesson(s, ru, questions_count=3)
            nxt3 = await ls.get_next_exercise(s, ml.id, ru.id)
            _, mex = nxt3
            ok("«Мои ошибки» начинаются с просроченного повтора", mex.id == rex.id)
            corr = next(o for o in mex.options if o.is_correct)
            await ls.answer_exercise(s, ru, ml.id, mex.id, corr.id)
        review = (await s.execute(select(UserExerciseReview).where(
            UserExerciseReview.user_id == ru.id, UserExerciseReview.exercise_id == rex.id))).scalar_one_or_none()
        ok("stage 2 пройден частично: интервал ~7 дней", review is not None and review.stage == 2)

        review.next_review_at = dt.utcnow() - __import__("datetime").timedelta(hours=1)
        await s.commit()
        ml2 = await ls.create_mistakes_lesson(s, ru, questions_count=3)
        nxt4 = await ls.get_next_exercise(s, ml2.id, ru.id)
        _, mex2 = nxt4
        corr = next(o for o in mex2.options if o.is_correct)
        await ls.answer_exercise(s, ru, ml2.id, mex2.id, corr.id)
        review = (await s.execute(select(UserExerciseReview).where(
            UserExerciseReview.user_id == ru.id, UserExerciseReview.exercise_id == rex.id))).scalar_one_or_none()
        ok("после 1-3-7 правило усвоено: запись удалена", review is None)

    print("== v3.2: сводка владельцу ==")
    fake2 = FakeBot()
    report_text = None
    async with async_session_factory() as s:
        report_text = await ns.build_admin_report(s)
    ok("сводка строится: пользователи/ответы/точность", "Новых пользователей" in report_text and "точность" in report_text)

    orig_due = ns.is_reminder_due
    ns.is_reminder_due = lambda *a, **k: True
    try:
        sent_a = await ns.send_admin_reports(fake2)
        sent_b = await ns.send_admin_reports(fake2)
    finally:
        ns.is_reminder_due = orig_due
    ok("сводка ушла админу один раз", sent_a == 1 and sent_b == 0 and fake2.sent and fake2.sent[0][0] == 111)

    await engine.dispose()
    print(f"\nИТОГО: {len(PASS)} проверок пройдено")

asyncio.run(main())
