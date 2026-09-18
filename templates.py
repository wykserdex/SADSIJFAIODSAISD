"""Шаблоны сообщений. {link} — плейсхолдер под твою ссылку."""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Добавляй свои шаблоны тут. Ключ — внутренний id, значение — текст.
TEMPLATES = {
    "promo": "👋 Привет! Посмотри, тут классная штука: {link}",
    "invite": "🔥 Приглашаю тебя сюда: {link}",
    "review": "Буду очень благодарен за отзыв/фидбек: {link}",
    "info": "📌 Полезная подборка по теме: {link}",
}


def get_template_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="📣 Промо", callback_data="tpl:promo")],
        [InlineKeyboardButton(text="🔥 Приглашение", callback_data="tpl:invite")],
        [InlineKeyboardButton(text="⭐ Отзыв", callback_data="tpl:review")],
        [InlineKeyboardButton(text="📌 Инфо", callback_data="tpl:info")],
        [InlineKeyboardButton(text="✍️ Свой текст", callback_data="tpl:custom")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
