"""Info-категория: откуда проект, чей форк, как поднять зеркало."""
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import config
from core.loader import load_categories
from pathlib import Path

INFO = {
    "id": "info",
    "title": "ℹ️ Инфо",
    "desc": "Откуда бот, версия, как поднять своё зеркало.",
}

router = Router()


def _cats_text() -> str:
    try:
        cats = load_categories(Path(__file__).parent.parent.parent / "categories")
        return ", ".join(c["info"].title for c in cats) or "—"
    except Exception:
        return "—"


async def enter(message: types.Message, state: FSMContext):
    await state.clear()
    fork = config.FORK_OF.strip() if config.FORK_OF else "— (это исходник)"
    owner = config.OWNER.strip() or "не указан"
    await message.answer(
        f"ℹ️ {config.INSTANCE_NAME} v{config.VERSION}\n"
        f"Владелец зеркала: {owner}\n"
        f"Исходник: {config.SOURCE_URL}\n"
        f"Форк от: {fork}\n"
        f"Категории: {_cats_text()}\n\n"
        "Это личное зеркало, не общий бот. Своё поднимается за 5 минут.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🪞 Как поднять зеркало", callback_data="info:mirror")],
            [InlineKeyboardButton(text="📂 Категории", callback_data="info:cats")],
        ]),
    )


@router.callback_query(F.data == "cat_enter:info")
async def on_enter(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await enter(callback.message, state)


@router.callback_query(F.data == "info:mirror")
async def on_mirror(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "🪞 Своё зеркало:\n"
        "1. Форкни репо с исходника\n"
        "2. Создай бота у @BotFather → BOT_TOKEN\n"
        "3. my.telegram.org → API_ID / API_HASH (только если нужен sender)\n"
        "4. Скопируй .env.example в .env, заполни:\n"
        "   BOT_TOKEN, ALLOWED_USERS=свой id, INSTANCE_NAME, OWNER, FORK_OF=откуда взял\n"
        "5. pip install -r requirements.txt → python bot.py\n\n"
        "Правила: SOURCE_URL не трогаешь, FORK_OF указываешь честно, "
        "ALLOWED_USERS только свой. Чужие зеркала не дрочим — каждый сидит в своём."
    )


@router.callback_query(F.data == "info:cats")
async def on_cats(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.answer("Жми 📂 Категории в нижнем меню.")
