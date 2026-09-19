"""Шаблон новой категории. Скопируй папку как categories/my_cat и поменяй INFO."""
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext

INFO = {
    "id": "example",
    "title": "🧩 Example",
    "desc": "Шаблон. Скопируй папку и напиши свою логику.",
}

router = Router()


async def enter(message: types.Message, state: FSMContext):
    await message.answer("🧩 Это шаблон категории. Скопируй categories/_example в categories/my_cat и пили свою.")


@router.callback_query(F.data == "cat_enter:example")
async def on_enter(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await enter(callback.message, state)
