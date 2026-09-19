"""Work-категория v2: SQLite-трекер через storage.CampaignStore (по owner_id)."""
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import config
from storage import CampaignStore

INFO = {
    "id": "work",
    "title": "🛠 Work",
    "desc": "Задачи в SQLite: добавить / список / готово / удалить.",
}

router = Router()
store = CampaignStore(config.CAMPAIGNS_DB)


class WorkStates(StatesGroup):
    ADD_TEXT = State()


def _menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить задачу", callback_data="work:add")],
        [InlineKeyboardButton(text="📋 Мои задачи", callback_data="work:list")],
    ])


async def enter(message: types.Message, state: FSMContext):
    await state.clear()
    items = store.list_tasks(message.from_user.id, limit=100)
    open_n = sum(1 for t in items if t["status"] == "open")
    await message.answer(
        f"🛠 Work — открытых: {open_n} / всего: {len(items)} (SQLite).\nЧто делаем?",
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
    store.create_task(message.from_user.id, message.text.strip()[:500])
    await state.clear()
    n = len(store.list_tasks(message.from_user.id, limit=100))
    await message.answer(f"✅ Добавил. Всего: {n}.", reply_markup=_menu())


@router.callback_query(F.data == "work:list")
async def on_list(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    items = store.list_tasks(callback.from_user.id, limit=20)
    if not items:
        await callback.message.answer("📭 Пока пусто. Жми «➕ Добавить».", reply_markup=_menu())
        return
    kb = []
    lines = []
    for t in items:
        tid = t["id"]
        mark = "✅" if t["status"] == "done" else "⬜"
        lines.append(f"{tid}. {mark} {(t['title'] or '')[:80]}")
        if t["status"] == "open":
            kb.append([
                InlineKeyboardButton(text=f"✅ {tid}", callback_data=f"work:done:{tid}"),
                InlineKeyboardButton(text=f"🗑 {tid}", callback_data=f"work:del:{tid}"),
            ])
        else:
            kb.append([InlineKeyboardButton(text=f"🗑 {tid}", callback_data=f"work:del:{tid}")])
    kb.append([InlineKeyboardButton(text="🗑 Очистить готовые", callback_data="work:clear_done")])
    await callback.message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@router.callback_query(F.data.startswith("work:done:"))
async def on_done(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        tid = int(callback.data.split(":")[2])
    except Exception:
        return
    store.complete_task(callback.from_user.id, tid)
    await on_list(callback, state)


@router.callback_query(F.data.startswith("work:del:"))
async def on_del(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        tid = int(callback.data.split(":")[2])
    except Exception:
        return
    store.delete_task(callback.from_user.id, tid)
    await on_list(callback, state)


@router.callback_query(F.data == "work:clear_done")
async def on_clear(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    items = store.list_tasks(callback.from_user.id, limit=100)
    n = 0
    for t in items:
        if t["status"] == "done":
            if store.delete_task(callback.from_user.id, t["id"]):
                n += 1
    left = len(store.list_tasks(callback.from_user.id, limit=100))
    await callback.message.answer(f"🗑 Удалил готовых: {n}. Осталось: {left}.", reply_markup=_menu())
