"""Мультитул-ядро: пустой сток, категории подхватываются из categories/*/category.py."""
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

import config
from core.loader import load_categories

logging.basicConfig(level=logging.INFO)
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
core_router = Router()

CATS = load_categories(Path(__file__).parent / "categories")
for c in CATS:
    dp.include_router(c["router"])
dp.include_router(core_router)

print(f"[core] loaded categories: {[c['info'].id for c in CATS]}")

core_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📂 Категории")]],
    resize_keyboard=True,
)


def _uid(update):
    if update.message:
        return update.message.from_user.id
    if update.callback_query:
        return update.callback_query.from_user.id
    if update.my_chat_member:
        return update.my_chat_member.from_user.id
    return None


async def access_middleware(handler, event, data):
    uid = _uid(event)
    if uid is None:
        return await handler(event, data)
    if not config.ALLOWED_USERS or uid in config.ALLOWED_USERS:
        return await handler(event, data)
    if event.message:
        await event.message.answer("⛔ У тебя нет доступа к этому боту.")
    elif event.callback_query:
        await event.callback_query.answer("⛔ Нет доступа", show_alert=True)
    return


dp.update.outer_middleware(access_middleware)


def categories_kb() -> InlineKeyboardMarkup:
    rows = []
    for c in CATS:
        rows.append([InlineKeyboardButton(text=c["info"].title, callback_data=f"cat_enter:{c['info'].id}")])
    if not rows:
        rows = [[InlineKeyboardButton(text="пусто", callback_data="cat:empty")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@core_router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    if not CATS:
        await message.answer("👋 Пусто. Добавь категорию в categories/ (смотри categories/_example).", reply_markup=core_kb)
        return
    titles = ", ".join(c["info"].title for c in CATS)
    await message.answer(
        f"👋 Мультитул. Категорий: {len(CATS)} ({titles}).\nЖми 📂 Категории.",
        reply_markup=core_kb,
    )


@core_router.message(F.text == "📂 Категории")
async def show_cats(message: types.Message, state: FSMContext):
    lines = ["📂 Категории:"]
    for c in CATS:
        lines.append(f"• {c['info'].title} — {c['info'].desc}")
    lines.append("\nНовая категория = папка categories/my_cat с category.py (шаблон в _example).")
    await message.answer("\n".join(lines), reply_markup=categories_kb())


@core_router.callback_query(F.data == "cat:empty")
async def cat_empty(callback: types.CallbackQuery):
    await callback.answer("Категорий пока нет", show_alert=True)


async def main():
    if not config.ALLOWED_USERS:
        logging.warning("ALLOWED_USERS пуст — бот доступен ВСЕМ. Укажи свои user_id в .env!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
