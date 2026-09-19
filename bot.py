"""aiogram-бот — управляющий интерфейс для постинга по своей сетке каналов/чатов."""
import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

import config
from templates import TEMPLATES, get_template_keyboard
from user_client import UserSender, parse_lines_to_targets
from telethon.errors import PhoneCodeExpiredError, PhoneCodeInvalidError, FloodWaitError

logging.basicConfig(level=logging.INFO)
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

CHATS_DIR = Path(__file__).parent / "chats"
CHATS_DIR.mkdir(exist_ok=True)


# ---------------- менеджер сессий (мультиаккаунт) ----------------
class SessionManager:
    def __init__(self):
        self._cache = {}

    def all_names(self):
        return sorted(p.stem for p in Path(__file__).parent.glob("sessions/*.session"))

    def get(self, name):
        if name not in self._cache:
            self._cache[name] = UserSender(name)
        return self._cache[name]


manager = SessionManager()


# ---------------- whitelist по user_id ----------------
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


# ---------------- состояния ----------------
class States(StatesGroup):
    MENU = State()
    ACC_NAME = State()
    PHONE = State()
    CODE = State()
    PASSWORD = State()
    WAIT_CHATS = State()
    MESSAGE = State()
    ACC_SELECT = State()
    API_ID_INPUT = State()
    API_HASH_INPUT = State()
    CONFIRM = State()


main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Создать сессию")],
        [KeyboardButton(text="⚙️ API-ключи")],
        [KeyboardButton(text="👥 Мои аккаунты")],
        [KeyboardButton(text="📥 Загрузить список чатов")],
        [KeyboardButton(text="✉️ Текст рассылки")],
        [KeyboardButton(text="🚀 Старт рассылки")],
        [KeyboardButton(text="🧪 Тест-прогон")],
        [KeyboardButton(text="📊 Статус")],
    ],
    resize_keyboard=True,
)


def make_bar(done, total, note, current):
    width = 20
    filled = int(width * done / total) if total else 0
    bar = "█" * filled + "░" * (width - filled)
    pct = int(100 * done / total) if total else 0
    lines = [
        f"📤 Рассылка  [{bar}]  {pct}%",
        f"➡️  Сейчас: {current}",
        f"📍  {done}/{total}",
    ]
    if note:
        lines.append(f"⚠️  {note}")
    return "\n".join(lines)


# ---------------- /start ----------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.set_state(States.MENU)
    n = len(manager.all_names())
    text = (
        f"👋 Добро пожаловать! В базе аккаунтов: {n}.\n"
        "Создай сессию (➕ Создать сессию) или выбери аккаунты (👥 Мои аккаунты)."
    )
    if not config.API_CONFIGURED:
        text += "\n\n⚠️ Сначала заполни API_ID/API_HASH — кнопка «⚙️ API-ключи»."
    await message.answer(text, reply_markup=main_kb)


# ---------------- создание сессии (именованной) ----------------
@dp.message(F.text == "➕ Создать сессию")
async def create_session(message: types.Message, state: FSMContext):
    if not config.API_CONFIGURED:
        await message.answer(
            "❌ Сначала заполни API_ID и API_HASH.\n"
            "Нажми «⚙️ API-ключи» и пришли их прямо в этом чате "
            "(берутся на https://my.telegram.org → API development tools, создать приложение)."
        )
        return
    existing = manager.all_names()
    hint = f"\nУже есть: {', '.join(existing)}" if existing else ""
    await message.answer("Введи имя для нового аккаунта (напр. acc1, work, spare):" + hint)
    await state.set_state(States.ACC_NAME)


# ---------------- интерактивный ввод API_ID / API_HASH ----------------
@dp.message(F.text == "⚙️ API-ключи")
async def api_settings(message: types.Message, state: FSMContext):
    if config.API_CONFIGURED:
        await message.answer(
            f"🔑 Текущие ключи:\nAPI_ID: {config.API_ID}\n"
            f"API_HASH: {config.API_HASH[:6]}…(скрыт)\n\n"
            "Пришли новый API_ID, чтобы перезаписать (или /cancel)."
        )
    else:
        await message.answer("Отправь API_ID (число) из my.telegram.org → API development tools:")
    await state.set_state(States.API_ID_INPUT)


@dp.message(States.API_ID_INPUT)
async def api_id_input(message: types.Message, state: FSMContext):
    if message.text.strip() == "/cancel":
        await state.set_state(States.MENU)
        await message.answer("❌ Отмена.", reply_markup=main_kb)
        return
    try:
        val = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Это должно быть числом. Пришли API_ID:")
        return
    await state.update_data(tmp_api_id=val)
    await message.answer("Теперь пришли API_HASH (строка):")
    await state.set_state(States.API_HASH_INPUT)


@dp.message(States.API_HASH_INPUT)
async def api_hash_input(message: types.Message, state: FSMContext):
    h = message.text.strip()
    if not h:
        await message.answer("❌ Пришли API_HASH:")
        return
    data = await state.get_data()
    config.save_settings({"API_ID": data["tmp_api_id"], "API_HASH": h})
    manager._cache.clear()  # чтобы Telethon-клиенты пересоздались с новыми ключами
    await state.set_state(States.MENU)
    await message.answer(
        "✅ API_ID/API_HASH сохранены в settings.json. Можно создавать сессию (➕ Создать сессию).",
        reply_markup=main_kb,
    )


@dp.message(States.ACC_NAME)
async def process_acc_name(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("Пришли текстом короткое имя аккаунта (напр. acc1).")
        return
    name = message.text.strip().replace(" ", "_")
    # только латиница / цифры / подчёркивание — чтобы кнопки и эмодзи не съелись как имя
    ok = bool(name) and all(c.isascii() and (c.isalnum() or c == "_") for c in name)
    if not ok:
        await message.answer(
            "❌ Имя — только латиница, цифры и подчёркивание (напр. acc1, work_2). "
            "Без пробелов, эмодзи и кириллицы. Введи заново:"
        )
        return
    await state.update_data(acc_name=name)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(
        f"Отправь номер аккаунта «{name}»:\n• кнопкой «📱 Поделиться номером», либо\n• текстом +375...",
        reply_markup=kb,
    )
    await state.set_state(States.PHONE)


@dp.message(States.PHONE)
async def process_phone(message: types.Message, state: FSMContext):
    if message.contact:
        phone = "+" + message.contact.phone_number.lstrip("+")
    elif message.text:
        phone = message.text.strip()
    else:
        await message.answer("Пришли номер текстом или кнопкой «📱 Поделиться номером».")
        return
    if not phone.startswith("+"):
        phone = "+" + phone

    data = await state.get_data()
    name = data["acc_name"]
    acc = manager.get(name)
    await state.update_data(phone=phone)
    try:
        await acc.connect()
        phone_hash = await acc.send_code(phone)
        await state.update_data(phone_code_hash=phone_hash)
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки кода: {e}")
        return
    await message.answer(
        "📨 Введите код подтверждения (из СМС или приложения Telegram):",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    await state.set_state(States.CODE)


@dp.message(States.CODE)
async def process_code(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("Пришли код текстом:")
        return
    code = message.text.strip()
    data = await state.get_data()
    name = data["acc_name"]
    phone = data["phone"]
    acc = manager.get(name)
    acc.phone_code_hash = data.get("phone_code_hash")  # на случай перезапуска бота
    try:
        result = await acc.sign_in(phone, code)
    except PhoneCodeExpiredError:
        # код истёк — запрашиваем новый автоматически
        try:
            await acc.send_code(phone)
            await state.update_data(phone_code_hash=acc.phone_code_hash)
        except Exception as e:
            await message.answer(f"❌ Не удалось запросить новый код: {e}")
            return
        await message.answer("⏳ Код истёк — я запросил новый. Введи его побыстрее (живёт ~60 сек):")
        return
    except PhoneCodeInvalidError:
        await message.answer("❌ Неверный код. Попробуй ещё раз:")
        return
    except FloodWaitError as e:
        await message.answer(f"⏳ FloodWait {e.seconds} сек — подожди и попробуй позже.")
        return
    except Exception as e:
        await message.answer(f"❌ Ошибка входа: {e}")
        return
    if result == "2fa":
        await message.answer("🔐 У аккаунта включена двухфакторка. Введи пароль:")
        await state.set_state(States.PASSWORD)
        return
    await message.answer(f"✅ Аккаунт «{name}» зарегистрирован!", reply_markup=main_kb)
    await state.set_state(States.MENU)


@dp.message(States.PASSWORD)
async def process_password(message: types.Message, state: FSMContext):
    data = await state.get_data()
    name = data["acc_name"]
    acc = manager.get(name)
    try:
        await acc.sign_in_password(message.text.strip())
    except Exception as e:
        await message.answer(f"❌ Неверный пароль: {e}")
        return
    await message.answer(f"✅ Аккаунт «{name}» зарегистрирован!", reply_markup=main_kb)
    await state.set_state(States.MENU)


# ---------------- выбор аккаунтов ----------------
@dp.message(F.text == "👥 Мои аккаунты")
async def accounts_menu(message: types.Message, state: FSMContext):
    names = manager.all_names()
    if not names:
        await message.answer("Пока нет аккаунтов. Создай через «➕ Создать сессию».")
        return
    data = await state.get_data()
    active = data.get("active_accounts")
    if active is None:
        active = list(names)
    msg = await message.answer("загрузка...")
    await _render_accounts(msg, names, set(active))
    await state.update_data(active_accounts=active)
    await state.set_state(States.ACC_SELECT)


async def _render_accounts(msg, names, active):
    kb = []
    for n in names:
        mark = "✅" if n in active else "⬜"
        kb.append([InlineKeyboardButton(text=f"{mark} {n}", callback_data=f"acc:{n}")])
    kb.append([
        InlineKeyboardButton(text="✅ Все", callback_data="acc:all"),
        InlineKeyboardButton(text="⬜ Ни одного", callback_data="acc:none"),
    ])
    kb.append([InlineKeyboardButton(text="💾 Готово", callback_data="acc:done")])
    await msg.edit_text(
        "👥 Аккаунты (✅ — будут слать). Выбери нужные:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )


@dp.callback_query(F.data.startswith("acc:"), States.ACC_SELECT)
async def acc_toggle(callback: types.CallbackQuery, state: FSMContext):
    action = callback.data.split(":", 1)[1]
    data = await state.get_data()
    names = manager.all_names()
    active = set(data.get("active_accounts") or names)

    if action == "all":
        active = set(names)
    elif action == "none":
        active = set()
    elif action == "done":
        await state.update_data(active_accounts=list(active))
        await callback.message.answer("💾 Выбор сохранён.", reply_markup=main_kb)
        await state.set_state(States.MENU)
        await callback.answer()
        return
    else:
        active.add(action) if action not in active else active.discard(action)
        await state.update_data(active_accounts=list(active))

    await _render_accounts(callback.message, names, active)
    await callback.answer()


# ---------------- список чатов ----------------
@dp.message(F.text == "📥 Загрузить список чатов")
async def load_chats(message: types.Message, state: FSMContext):
    await message.answer("📎 Пришли .txt файл со ссылками на чаты — по одной на строку.")
    await state.set_state(States.WAIT_CHATS)


@dp.message(F.document, States.WAIT_CHATS)
@dp.message(F.document, States.MENU)
async def process_chats_file(message: types.Message, state: FSMContext):
    doc = message.document
    if not (doc.file_name or "").endswith(".txt"):
        await message.answer("❌ Пришли именно .txt файл со ссылками.")
        return
    path = CHATS_DIR / f"{message.from_user.id}.txt"
    await bot.download(doc, destination=path)
    targets = parse_lines_to_targets(path.read_text(encoding="utf-8", errors="ignore"))
    await state.update_data(chats_path=str(path), chats_count=len(targets))
    await message.answer(
        f"📥 Прочитано ссылок: {len(targets)}.\nТеперь задай текст (кнопка «✉️ Текст рассылки»).",
        reply_markup=main_kb,
    )
    await state.set_state(States.MENU)


# ---------------- текст рассылки ----------------
@dp.message(F.text == "✉️ Текст рассылки")
async def choose_message(message: types.Message, state: FSMContext):
    await message.answer(
        "Выбери шаблон (ссылку потом подставишь) или «✍️ Свой текст»:",
        reply_markup=get_template_keyboard(),
    )
    await state.set_state(States.MESSAGE)


@dp.callback_query(F.data.startswith("tpl:"), States.MESSAGE)
async def template_chosen(callback: types.CallbackQuery, state: FSMContext):
    key = callback.data.split(":", 1)[1]
    if key == "custom":
        await callback.message.answer("✍️ Введи свой текст сообщения:")
        await state.update_data(template=None)
        await callback.answer()
        return
    template = TEMPLATES[key]
    await state.update_data(template=template)
    if "{link}" in template:
        await callback.message.answer("🔗 Введи ссылку, которая заменит {link} в шаблоне:")
    else:
        await state.update_data(final_text=template)
        await callback.message.answer("✅ Текст сохранён. Можно запускать рассылку.", reply_markup=main_kb)
        await state.set_state(States.MENU)
    await callback.answer()


@dp.message(States.MESSAGE)
async def process_message_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    template = data.get("template")
    if template is None:
        await state.update_data(final_text=message.text)
        await message.answer("✅ Текст сохранён.", reply_markup=main_kb)
        await state.set_state(States.MENU)
        return
    link = message.text.strip()
    final = template.replace("{link}", link)
    await state.update_data(final_text=final)
    await message.answer(f"✅ Итоговый текст:\n\n{final}", reply_markup=main_kb)
    await state.set_state(States.MENU)


# ---------------- запуск рассылки (только по своим каналам/чатам) ----------------
@dp.message(F.text == "🚀 Старт рассылки")
async def start_sending(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("chats_path") or not data.get("final_text"):
        await message.answer("⚠️ Сначала загрузи список чатов и задай текст.")
        return
    targets = parse_lines_to_targets(
        Path(data["chats_path"]).read_text(encoding="utf-8", errors="ignore")
    )
    if not targets:
        await message.answer("⚠️ Список чатов пуст.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, начать", callback_data="send:go"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="send:cancel"),
    ]])
    active = data.get("active_accounts") or manager.all_names()
    await message.answer(
        f"🚀 Запустить рассылку?\n\nЧатов: {len(targets)}\n"
        f"Аккаунтов: {len(active)}\nТекст:\n{data['final_text'][:200]}",
        reply_markup=kb,
    )
    await state.set_state(States.CONFIRM)


@dp.callback_query(F.data == "send:go", States.CONFIRM)
async def do_send(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await _launch(callback.message, state, dry_run=bool(config.TEST_MODE))


@dp.message(F.text == "🧪 Тест-прогон")
async def test_run(message: types.Message, state: FSMContext):
    await _launch(message, state, dry_run=True)


@dp.callback_query(F.data == "send:cancel", States.CONFIRM)
async def cancel_send(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("❌ Отменено.", reply_markup=main_kb)
    await state.set_state(States.MENU)


async def _launch(message: types.Message, state: FSMContext, dry_run: bool):
    data = await state.get_data()
    chats_path = data.get("chats_path")
    final_text = data.get("final_text")
    if not chats_path or not final_text:
        await message.answer("⚠️ Сначала загрузи список чатов и задай текст.")
        return

    targets = parse_lines_to_targets(
        Path(chats_path).read_text(encoding="utf-8", errors="ignore")
    )
    if not targets:
        await message.answer("⚠️ Список чатов пуст.")
        return

    # выбранные аккаунты
    chosen = data.get("active_accounts") or manager.all_names()
    chosen = [n for n in chosen if n in set(manager.all_names())]
    if not chosen:
        await message.answer("⚠️ Нет выбранных аккаунтов. Зайди в «👥 Мои аккаунты».")
        return

    accs = [manager.get(n) for n in chosen]

    # для реальной отправки — только авторизованные
    if not dry_run:
        valid = []
        for acc in accs:
            try:
                await acc.connect()
                if await acc.is_authorized():
                    valid.append(acc)
            except Exception:
                pass
        if not valid:
            await message.answer("❌ Нет авторизованных аккаунтов среди выбранных.")
            return
        accs = valid

    # распределяем чаты между аккаунтами (каждый чат — одно сообщение от одного аккаунта)
    n = len(accs)
    shares = [targets[i::n] for i in range(n)]

    tag = "🧪 ТЕСТ" if dry_run else "📤"
    total = len(targets)
    progress_msg = await message.answer(f"{tag}: подготовка...")
    per_acc = {}
    agg = {"sent": 0, "skipped": 0, "failed": 0}
    lock = asyncio.Lock()

    async def render(note, current):
        async with lock:
            done = sum(per_acc.values())
        try:
            await progress_msg.edit_text(make_bar(done, total, note, current))
        except Exception:
            pass

    async def worker(acc, share, acc_name):
        async def cb(idx, total_share, note, current):
            async with lock:
                per_acc[acc_name] = idx
            await render(note, f"{acc_name} → {current}")

        stats = await acc.send_to_chats(share, final_text, config.DELAY, cb, dry_run=dry_run)
        async with lock:
            agg["sent"] += stats["sent"]
            agg["skipped"] += stats["skipped"]
            agg["failed"] += stats["failed"]
            per_acc[acc_name] = len(share)
        await render(None, f"{acc_name}: финиш")

    await asyncio.gather(
        *(worker(acc, share, chosen[i]) for i, (acc, share) in enumerate(zip(accs, shares)))
    )

    await message.answer(
        f"{tag} Готово!\n✅ Отправлено: {agg['sent']}\n"
        f"⏭ Пропущено: {agg['skipped']}\n❌ Ошибок: {agg['failed']}\n"
        f"👥 Аккаунтов задействовано: {n}",
        reply_markup=main_kb,
    )
    await state.set_state(States.MENU)


# ---------------- статус ----------------
@dp.message(F.text == "📊 Статус")
async def status(message: types.Message, state: FSMContext):
    names = manager.all_names()
    lines = [f"👥 Аккаунтов в базе: {len(names)}"]
    for n in names:
        acc = manager.get(n)
        try:
            await acc.connect()
            ok = await acc.is_authorized()
        except Exception:
            ok = False
        lines.append(f"  • {n}: {'✅ авторизован' if ok else '❌ не авторизован'}")
    data = await state.get_data()
    if data.get("chats_count"):
        lines.append(f"📥 Чатов загружено: {data['chats_count']}")
    if data.get("final_text"):
        lines.append(f"✉️ Текст задан: да ({len(data['final_text'])} симв.)")
    active = data.get("active_accounts") or names
    lines.append(f"🎯 Выбрано для рассылки: {', '.join(active) if active else '—'}")
    await message.answer("\n".join(lines), reply_markup=main_kb)


# ---------------- фолбэк: в ЛС бот никогда не молчит (только в MENU, чтобы не рвать ввод кода/номера) ----------------
@dp.message(F.chat.type == "private", States.MENU)
async def fallback_private(message: types.Message, state: FSMContext):
    await state.set_state(States.MENU)
    await message.answer(
        "👋 Привет! Нажми /start или используй кнопки меню ниже.",
        reply_markup=main_kb,
    )


async def main():
    if not config.ALLOWED_USERS:
        logging.warning(
            "ALLOWED_USERS пуст — бот доступен ВСЕМ. Укажи свои user_id в .env!"
        )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
