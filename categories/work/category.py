"""Work-категория: мини таск-менеджер по юзеру. Пример как писать свои категории."""
import json
from pathlib import Path

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

INFO = {
    "id": "work",
    "title": "🛠 Work",
    "desc": "Задачи: добавить / список / готово / удалить. Хранится в data/.",
}

router = Router()

DATA_ROOT = Path(__file__).parent.parent.parent / "data"
DATA_ROOT.mkdir(exist_ok=True)


class WorkStates(StatesGroup):
    ADD_TEXT = State()


def _path(uid: int) -> Path:
    return DATA_ROOT / f"work_{uid}.json"


def _load(uid: int) -> list:
    p = _path(uid)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(uid: int, items: list):
    _path(uid).write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить задачу", callback_data="work:add")],
        [InlineKeyboardButton(text="📋 Мои задачи", callback_data="work:list")],
    ])


async def enter(message: types.Message, state: FSMContext):
    await state.clear()
    items = _load(message.from_user.id)
    open_n = sum(1 for t in items if not t.get("done"))
    await message.answer(
        f"🛠 Work — открытых: {open_n} / всего: {len(items)}.\nЧто делаем?",
        reply_markup=_menu(),
    )


@router.callback_query(F.data == "cat_enter:work")
async def on_enter(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await enter(callback.message, state)


@router.callback_query(F.data == "work:add")
async def on_add(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(WorkStates.ADD_TEXT)
    await callback.message.answer("✍️ Пришли текст задачи (или /cancel):")


@router.message(WorkStates.ADD_TEXT)
async def on_add_text(message: types.Message, state: FSMContext):
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ Отмена.", reply_markup=_menu())
        return
    items = _load(message.from_user.id)
    items.append({"text": message.text.strip()[:500], "done": False})
    _save(message.from_user.id, items)
    await state.clear()
    await message.answer(f"✅ Добавил. Всего: {len(items)}.", reply_markup=_menu())


@router.callback_query(F.data == "work:list")
async def on_list(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    items = _load(callback.from_user.id)
    if not items:
        await callback.message.answer("📭 Пока пусто. Жми «➕ Добавить».", reply_markup=_menu())
        return
    kb = []
    lines = []
    for i, t in enumerate(items):
        mark = "✅" if t.get("done") else "⬜"
        lines.append(f"{i+1}. {mark} {t.get('text','')[:80]}")
        kb.append([InlineKeyboardButton(
            text=f"{'↩️' if t.get('done') else '✅'} {i+1}",
            callback_data=f"work:toggle:{i}",
        )])
    kb.append([InlineKeyboardButton(text="🗑 Очистить готовые", callback_data="work:clear_done")])
    await callback.message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@router.callback_query(F.data.startswith("work:toggle:"))
async def on_toggle(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        idx = int(callback.data.split(":")[2])
    except Exception:
        return
    items = _load(callback.from_user.id)
    if 0 <= idx < len(items):
        items[idx]["done"] = not items[idx].get("done")
        _save(callback.from_user.id, items)
    await on_list(callback, state)


@router.callback_query(F.data == "work:clear_done")
async def on_clear(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    items = [t for t in _load(callback.from_user.id) if not t.get("done")]
    _save(callback.from_user.id, items)
    await callback.message.answer(f"🗑 Готово. Осталось: {len(items)}.", reply_markup=_menu())
