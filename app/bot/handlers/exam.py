from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.seed_passages import PASSAGES
from app.services import essay_service, oge_service
from app.services.essay_ai_service import ai_available, grade_essay
from app.services.exam_score_service import format_score_report, task_readiness
from app.services.user_service import get_or_create_user

router = Router()

_LETTERS = ["А", "Б", "В", "Г", "Д"]


class EssayStates(StatesGroup):
    waiting_text = State()


class EssayAiStates(StatesGroup):
    waiting_text = State()


class OgeStates(StatesGroup):
    waiting_text = State()


class DrillStates(StatesGroup):
    answering = State()


DRILLS: list[tuple[str, set[str], str]] = [
    ("Запиши форму родительного падежа мн. ч.: «пять (помидоры)».",
     {"помидоров"}, "Помидоры -> помидОров (с окончанием -ов)."),
    ("Запиши верную форму повелительного наклонения от глагола «ехать».",
     {"поезжай", "езжай"}, "Верно: поезжай или езжай. Форм «ехай», «едь» в норме нет."),
    ("Сколько Н? Запиши прилагательное целиком: «кожа_ый».",
     {"кожаный"}, "Суффикс -ан- -> одна Н: кожаный."),
    ("Запиши слово с приставкой слитно: «бе_конечный».",
     {"бесконечный"}, "Перед глухим согласным в приставке пишем С: бесконечный."),
    ("«в течение» или «в течении»? Запиши предлог времени (2 слова).",
     {"в течение"}, "Предлог времени - «в течение» (на конце Е)."),
    ("Запиши родительный падеж мн. ч.: «нет (чулки)».",
     {"чулок"}, "Чулки -> чулОк (без окончания). А носки -> носкОв."),
]


def _normalize(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


# ----------------------------- keyboards ---------------------------------
def _score_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="✍️ Впиши ответ (как на ЕГЭ)", callback_data="drill:start")
    builder.button(text="📖 Текст с заданиями", callback_data="passage:start")
    builder.button(text="📝 Сочинение", callback_data="essay:menu")
    builder.button(text="🏠 Меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def _essay_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🧱 Структура", callback_data="essay:structure")
    builder.button(text="📋 Критерии К1-К10", callback_data="essay:criteria")
    builder.button(text="🧩 Клише", callback_data="essay:cliche")
    builder.button(text="🗂 Банк проблем", callback_data="essay:problems")
    builder.button(text="✅ Чек-лист", callback_data="essay:checklist")
    builder.button(text="🔎 Проверить объем", callback_data="essay:check")
    builder.button(text="🤖 AI-проверка (бета)", callback_data="essay:ai")
    builder.button(text="🏠 Меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def _essay_back_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ К сочинению", callback_data="essay:menu")
    builder.adjust(1)
    return builder.as_markup()


def _oge_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="ℹ️ Из чего состоит", callback_data="oge:about")
    builder.button(text="🎧 Изложение", callback_data="oge:izl")
    builder.button(text="✍️ Сочинение 9.1", callback_data="oge:91")
    builder.button(text="✍️ Сочинение 9.2", callback_data="oge:92")
    builder.button(text="✍️ Сочинение 9.3", callback_data="oge:93")
    builder.button(text="📋 Критерии", callback_data="oge:criteria")
    builder.button(text="🔎 Проверить объем", callback_data="oge:check")
    builder.button(text="🏠 Меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def _oge_back_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ К ОГЭ", callback_data="oge:menu")
    builder.adjust(1)
    return builder.as_markup()


# ----------------------------- score -------------------------------------
async def _send_score(message: Message, session: AsyncSession, telegram_user) -> None:
    user = await get_or_create_user(session, telegram_user)
    items, projected, covered = await task_readiness(session, user)
    await message.answer(format_score_report(items, projected, covered), reply_markup=_score_keyboard())


@router.message(F.text == "📊 Мой балл")
async def score_message(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    await _send_score(message, session, message.from_user)


@router.callback_query(F.data == "score:show")
async def score_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if isinstance(callback.message, Message):
        await _send_score(callback.message, session, callback.from_user)
    await callback.answer()


# ----------------------------- сочинение №27 -----------------------------
@router.message(F.text == "📝 Сочинение")
async def essay_message(message: Message) -> None:
    await message.answer(
        "📝 <b>Сочинение №27</b>\n\nЭто почти половина балла ЕГЭ (22 из 50). Выбери раздел:",
        reply_markup=_essay_menu_keyboard(),
    )


@router.callback_query(F.data == "essay:menu")
async def essay_menu_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer("📝 <b>Сочинение №27</b>. Выбери раздел:", reply_markup=_essay_menu_keyboard())
    await callback.answer()


_ESSAY_SECTIONS = {
    "essay:structure": essay_service.STRUCTURE,
    "essay:criteria": essay_service.CRITERIA,
    "essay:cliche": essay_service.CLICHE,
    "essay:problems": essay_service.PROBLEMS,
    "essay:checklist": essay_service.CHECKLIST,
}


@router.callback_query(F.data.in_(_ESSAY_SECTIONS.keys()))
async def essay_section_callback(callback: CallbackQuery) -> None:
    text = _ESSAY_SECTIONS.get(callback.data or "")
    if text and isinstance(callback.message, Message):
        await callback.message.answer(text, reply_markup=_essay_back_keyboard())
    await callback.answer()


@router.callback_query(F.data == "essay:check")
async def essay_check_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(EssayStates.waiting_text)
    if isinstance(callback.message, Message):
        await callback.message.answer("Пришли текст сочинения одним сообщением - посчитаю объем и дам чек-лист.")
    await callback.answer()


@router.message(EssayStates.waiting_text)
async def essay_check_process(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пустой текст. Пришли сочинение текстом - и я проверю объем.")
        return
    await message.answer(essay_service.check_essay(text), reply_markup=_essay_menu_keyboard())


@router.callback_query(F.data == "essay:ai")
async def essay_ai_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not ai_available():
        if isinstance(callback.message, Message):
            await callback.message.answer(
                "🤖 AI-проверка пока выключена (включается владельцем). "
                "Пока проверь себя по критериям - кнопка «📋 Критерии К1-К10».",
                reply_markup=_essay_back_keyboard(),
            )
        await callback.answer()
        return
    await state.set_state(EssayAiStates.waiting_text)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Пришли текст сочинения одним сообщением. ИИ даст предварительную оценку по К1-К10.\n"
            "<i>Это ориентир, официальный балл ставит эксперт.</i>"
        )
    await callback.answer()


@router.message(EssayAiStates.waiting_text)
async def essay_ai_process(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пустой текст. Пришли сочинение текстом.")
        return
    await message.answer("🤖 Проверяю сочинение, это займет несколько секунд...")
    feedback = await grade_essay(text)
    await message.answer(feedback, reply_markup=_essay_menu_keyboard())


# ----------------------------- ОГЭ ---------------------------------------
@router.message(F.text == "🎓 ОГЭ")
async def oge_message(message: Message) -> None:
    await message.answer("🎓 <b>ОГЭ по русскому</b>. Выбери раздел:", reply_markup=_oge_menu_keyboard())


@router.callback_query(F.data == "oge:menu")
async def oge_menu_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer("🎓 <b>ОГЭ по русскому</b>. Выбери раздел:", reply_markup=_oge_menu_keyboard())
    await callback.answer()


_OGE_SECTIONS = {
    "oge:about": oge_service.ABOUT,
    "oge:izl": oge_service.IZLOZHENIE,
    "oge:91": oge_service.SOCH_91,
    "oge:92": oge_service.SOCH_92,
    "oge:93": oge_service.SOCH_93,
    "oge:criteria": oge_service.CRITERIA_NOTE,
}


@router.callback_query(F.data.in_(_OGE_SECTIONS.keys()))
async def oge_section_callback(callback: CallbackQuery) -> None:
    text = _OGE_SECTIONS.get(callback.data or "")
    if text and isinstance(callback.message, Message):
        await callback.message.answer(text, reply_markup=_oge_back_keyboard())
    await callback.answer()


@router.callback_query(F.data == "oge:check")
async def oge_check_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OgeStates.waiting_text)
    if isinstance(callback.message, Message):
        await callback.message.answer("Пришли текст изложения или сочинения - проверю объем (нужно >= 70 слов).")
    await callback.answer()


@router.message(OgeStates.waiting_text)
async def oge_check_process(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пустой текст. Пришли работу текстом.")
        return
    await message.answer(oge_service.check_text(text), reply_markup=_oge_menu_keyboard())


# ----------------------------- тренировка ввода --------------------------
async def _send_drill_question(message: Message, index: int) -> None:
    prompt = DRILLS[index][0]
    await message.answer(
        f"✍️ <b>Вопрос {index + 1} из {len(DRILLS)}</b>\n\n{prompt}\n\n<i>Впиши ответ сообщением.</i>"
    )


@router.callback_query(F.data == "drill:start")
async def drill_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(DrillStates.answering)
    await state.update_data(drill_index=0, drill_correct=0)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "✍️ <b>Тренировка ввода</b>: отвечай как на ЕГЭ - впиши слово или форму с клавиатуры."
        )
        await _send_drill_question(callback.message, 0)
    await callback.answer()


@router.message(DrillStates.answering)
async def drill_answer(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    index = int(data.get("drill_index", 0))
    correct = int(data.get("drill_correct", 0))
    if index >= len(DRILLS):
        await state.clear()
        return

    _, accepted, explanation = DRILLS[index]
    if _normalize(message.text or "") in accepted:
        correct += 1
        verdict = "✅ Верно!"
    else:
        verdict = f"❌ Не совсем. Правильно: <b>{sorted(accepted)[0]}</b>"
    await message.answer(f"{verdict}\n\n{explanation}")

    index += 1
    if index < len(DRILLS):
        await state.update_data(drill_index=index, drill_correct=correct)
        await _send_drill_question(message, index)
    else:
        await state.clear()
        await message.answer(
            f"🏁 <b>Готово!</b> Верно: <b>{correct} из {len(DRILLS)}</b>.\n\n"
            "Это формат краткого ответа ЕГЭ - тренируй ввод, а не только выбор.",
            reply_markup=_score_keyboard(),
        )


# ----------------------------- текст с заданиями -------------------------
def _passage_task_keyboard(p_idx: int, t_idx: int, n_options: int):
    builder = InlineKeyboardBuilder()
    for i in range(n_options):
        builder.button(text=_LETTERS[i], callback_data=f"ptans:{p_idx}:{t_idx}:{i}")
    builder.adjust(n_options if n_options <= 4 else 3)
    return builder.as_markup()


async def _send_passage_task(message: Message, p_idx: int, t_idx: int, with_text: bool) -> None:
    passage = PASSAGES[p_idx]
    tasks = passage["tasks"]
    parts = []
    if with_text:
        parts.append(f"📖 <b>{passage['title']}</b>\n\n{passage['text']}")
    task = tasks[t_idx]
    option_lines = "\n".join(
        f"<b>{_LETTERS[i]})</b> {opt[0]}" for i, opt in enumerate(task["options"])
    )
    parts.append(f"<b>Вопрос {t_idx + 1}/{len(tasks)}.</b> {task['question']}\n\n{option_lines}")
    await message.answer("\n\n".join(parts), reply_markup=_passage_task_keyboard(p_idx, t_idx, len(task["options"])))


@router.callback_query(F.data == "passage:start")
async def passage_start(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await _send_passage_task(callback.message, 0, 0, with_text=True)
    await callback.answer()


@router.callback_query(F.data.startswith("passage:show:"))
async def passage_show(callback: CallbackQuery) -> None:
    try:
        p_idx = int((callback.data or "").split(":")[2])
    except (ValueError, IndexError):
        await callback.answer()
        return
    if 0 <= p_idx < len(PASSAGES) and isinstance(callback.message, Message):
        await _send_passage_task(callback.message, p_idx, 0, with_text=True)
    await callback.answer()


@router.callback_query(F.data.startswith("ptans:"))
async def passage_answer(callback: CallbackQuery) -> None:
    try:
        _, p_raw, t_raw, o_raw = (callback.data or "").split(":")
        p_idx, t_idx, opt = int(p_raw), int(t_raw), int(o_raw)
        task = PASSAGES[p_idx]["tasks"][t_idx]
    except (ValueError, IndexError):
        await callback.answer("Не удалось обработать ответ.", show_alert=True)
        return

    options = task["options"]
    correct_idx = next(i for i, x in enumerate(options) if x[1])
    is_right = opt == correct_idx
    verdict = "✅ Верно!" if is_right else f"❌ Мимо. Верно: {options[correct_idx][0]}"

    builder = InlineKeyboardBuilder()
    tasks_total = len(PASSAGES[p_idx]["tasks"])
    if t_idx + 1 < tasks_total:
        builder.button(text="Дальше →", callback_data=f"ptnext:{p_idx}:{t_idx + 1}")
    elif p_idx + 1 < len(PASSAGES):
        builder.button(text="📖 Следующий текст", callback_data=f"passage:show:{p_idx + 1}")
    builder.button(text="🏠 Меню", callback_data="menu:main")
    builder.adjust(1)

    if isinstance(callback.message, Message):
        await callback.message.answer(f"<b>{verdict}</b>\n\n{task['short']}", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("ptnext:"))
async def passage_next(callback: CallbackQuery) -> None:
    try:
        _, p_raw, t_raw = (callback.data or "").split(":")
        p_idx, t_idx = int(p_raw), int(t_raw)
    except (ValueError, IndexError):
        await callback.answer()
        return
    if isinstance(callback.message, Message):
        await _send_passage_task(callback.message, p_idx, t_idx, with_text=False)
    await callback.answer()
