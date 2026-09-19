"""Sender-категория: постинг по своей сетке + история/план/стоп в SQLite."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

import config
from storage import CampaignStore
from templates import TEMPLATES, get_template_keyboard
from user_client import UserSender, parse_lines_report, parse_lines_to_targets
from telethon.errors import PhoneCodeExpiredError, PhoneCodeInvalidError, FloodWaitError

INFO = {
    "id": "sender",
    "title": "📤 Sender",
    "desc": "Постинг по своей сетке. Только свои чаты, где акк админ.",
}

router = Router()

CHATS_DIR = Path(__file__).parent.parent.parent / "data" / "chats"
CHATS_DIR.mkdir(parents=True, exist_ok=True)


class SessionManager:
    def __init__(self):
        self._cache = {}

    def all_names(self):
        return sorted(p.stem for p in Path(__file__).parent.parent.parent.glob("sessions/*.session"))

    def get(self, name):
        if name not in self._cache:
            self._cache[name] = UserSender(name)
        return self._cache[name]


manager = SessionManager()
cstore = CampaignStore(config.CAMPAIGNS_DB)
RUNNERS: dict[int, asyncio.Event] = {}
SCHEDULED: dict[int, asyncio.Task] = {}


class SenderStates(StatesGroup):
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
    SCHED_MINUTES = State()


sender_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Создать сессию")],
        [KeyboardButton(text="⚙️ API-ключи")],
        [KeyboardButton(text="👥 Мои аккаунты")],
        [KeyboardButton(text="📥 Загрузить список чатов")],
        [KeyboardButton(text="✉️ Текст рассылки")],
        [KeyboardButton(text="🚀 Старт рассылки")],
        [KeyboardButton(text="🕒 Запланировать")],
        [KeyboardButton(text="🧪 Тест-прогон")],
        [KeyboardButton(text="🛑 Остановить")],
        [KeyboardButton(text="📜 История")],
        [KeyboardButton(text="📊 Sender-статус")],
        [KeyboardButton(text="⬅️ В категории")],
    ],
    resize_keyboard=True,
)

core_hint_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📂 Категории")]],
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


async def enter(message: types.Message, state: FSMContext):
    await state.clear()
    n = len(manager.all_names())
    await message.answer(
        f"📤 Sender — своих сессий: {n}.\n"
        "Только своя сетка, где акк админ. Чужие чаты уйдут в skipped.",
        reply_markup=sender_kb,
    )


@router.callback_query(F.data == "cat_enter:sender")
async def on_enter(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await enter(callback.message, state)


@router.message(F.text == "⬅️ В категории")
async def back_to_core(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Ок, жми 📂 Категории.", reply_markup=core_hint_kb)


@router.message(F.text == "➕ Создать сессию")
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
    await state.set_state(SenderStates.ACC_NAME)


@router.message(F.text == "⚙️ API-ключи")
async def api_settings(message: types.Message, state: FSMContext):
    if config.API_CONFIGURED:
        await message.answer(
            f"🔑 Текущие ключи:\nAPI_ID: {config.API_ID}\n"
            f"API_HASH: {config.API_HASH[:6]}…(скрыт)\n\n"
            "Пришли новый API_ID, чтобы перезаписать (или /cancel)."
        )
    else:
        await message.answer("Отправь API_ID (число) из my.telegram.org → API development tools:")
    await state.set_state(SenderStates.API_ID_INPUT)


@router.message(SenderStates.API_ID_INPUT)
async def api_id_input(message: types.Message, state: FSMContext):
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ Отмена.", reply_markup=sender_kb)
        return
    try:
        val = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Это должно быть числом. Пришли API_ID:")
        return
    await state.update_data(tmp_api_id=val)
    await message.answer("Теперь пришли API_HASH (строка):")
    await state.set_state(SenderStates.API_HASH_INPUT)


@router.message(SenderStates.API_HASH_INPUT)
async def api_hash_input(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("❌ Пришли API_HASH:")
        return
    h = message.text.strip()
    if not h:
        await message.answer("❌ Пришли API_HASH:")
        return
    data = await state.get_data()
    config.save_settings({"API_ID": data["tmp_api_id"], "API_HASH": h})
    manager._cache.clear()
    await state.clear()
    await message.answer(
        "✅ API_ID/API_HASH сохранены в settings.json. Можно создавать сессию (➕ Создать сессию).",
        reply_markup=sender_kb,
    )


@router.message(SenderStates.ACC_NAME)
async def process_acc_name(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("Пришли текстом короткое имя аккаунта (напр. acc1).")
        return
    name = message.text.strip().replace(" ", "_")
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
    await state.set_state(SenderStates.PHONE)


@router.message(SenderStates.PHONE)
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
    await state.set_state(SenderStates.CODE)


@router.message(SenderStates.CODE)
async def process_code(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("Пришли код текстом:")
        return
    code = message.text.strip()
    data = await state.get_data()
    name = data["acc_name"]
    phone = data["phone"]
    acc = manager.get(name)
    acc.phone_code_hash = data.get("phone_code_hash")
    try:
        result = await acc.sign_in(phone, code)
    except PhoneCodeExpiredError:
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
        await state.set_state(SenderStates.PASSWORD)
        return
    await message.answer(f"✅ Аккаунт «{name}» зарегистрирован!", reply_markup=sender_kb)
    await state.clear()


@router.message(SenderStates.PASSWORD)
async def process_password(message: types.Message, state: FSMContext):
    data = await state.get_data()
    name = data["acc_name"]
    acc = manager.get(name)
    try:
        await acc.sign_in_password((message.text or "").strip())
    except Exception as e:
        await message.answer(f"❌ Неверный пароль: {e}")
        return
    await message.answer(f"✅ Аккаунт «{name}» зарегистрирован!", reply_markup=sender_kb)
    await state.clear()


@router.message(F.text == "👥 Мои аккаунты")
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
    await state.set_state(SenderStates.ACC_SELECT)


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


@router.callback_query(F.data.startswith("acc:"), SenderStates.ACC_SELECT)
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
        await callback.message.answer("💾 Выбор сохранён.", reply_markup=sender_kb)
        await state.set_state(None)
        await callback.answer()
        return
    else:
        active.add(action) if action not in active else active.discard(action)
        await state.update_data(active_accounts=list(active))

    await _render_accounts(callback.message, names, active)
    await callback.answer()


@router.message(F.text == "📥 Загрузить список чатов")
async def load_chats(message: types.Message, state: FSMContext):
    await message.answer("📎 Пришли .txt файл со ссылками на СВОИ чаты — по одной на строку.")
    await state.set_state(SenderStates.WAIT_CHATS)


@router.message(F.document)
async def process_chats_file(message: types.Message, state: FSMContext):
    data = await state.get_data()
    # принимаем файл только если ждали его; иначе игнор чтобы не мешать другим категориям
    cur = await state.get_state()
    if cur not in (SenderStates.WAIT_CHATS, "SenderStates:WAIT_CHATS"):
        # в aiogram 3 get_state возвращает "SenderStates:WAIT_CHATS"
        # пропустим если это не наш флоу
        if cur != SenderStates.WAIT_CHATS:
            pass
    if cur is None:
        return
    # строгая проверка: только WAIT_CHATS
    if str(cur).endswith("WAIT_CHATS") is False:
        return
    doc = message.document
    if not (doc.file_name or "").endswith(".txt"):
        await message.answer("❌ Пришли именно .txt файл со ссылками.")
        return
    # бот-объект получим через message.bot
    path = CHATS_DIR / f"{message.from_user.id}.txt"
    await message.bot.download(doc, destination=path)
    raw = path.read_text(encoding="utf-8", errors="ignore")
    targets, dup, invalid = parse_lines_report(raw)
    await state.update_data(chats_path=str(path), chats_count=len(targets))
    extra = ""
    if dup:
        extra += f" Дубликатов выкинуто: {dup}."
    if invalid:
        extra += f" Невалидных строк: {invalid}."
    await message.answer(
        f"📥 Чистых целей: {len(targets)}.{extra}\nТеперь задай текст (кнопка «✉️ Текст рассылки»).",
        reply_markup=sender_kb,
    )
    await state.set_state(None)


@router.message(F.text == "✉️ Текст рассылки")
async def choose_message(message: types.Message, state: FSMContext):
    await message.answer(
        "Выбери шаблон (ссылку потом подставишь) или «✍️ Свой текст»:",
        reply_markup=get_template_keyboard(),
    )
    await state.set_state(SenderStates.MESSAGE)


@router.callback_query(F.data.startswith("tpl:"), SenderStates.MESSAGE)
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
        await callback.message.answer("✅ Текст сохранён. Можно запускать рассылку.", reply_markup=sender_kb)
        await state.set_state(None)
    await callback.answer()


@router.message(SenderStates.MESSAGE)
async def process_message_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    template = data.get("template")
    if template is None:
        await state.update_data(final_text=message.text)
        await message.answer("✅ Текст сохранён.", reply_markup=sender_kb)
        await state.set_state(None)
        return
    link = (message.text or "").strip()
    final = template.replace("{link}", link)
    await state.update_data(final_text=final)
    await message.answer(f"✅ Итоговый текст:\n\n{final}", reply_markup=sender_kb)
    await state.set_state(None)


@router.message(F.text == "🚀 Старт рассылки")
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
        f"🚀 Запустить постинг по своим?\n\nЧатов: {len(targets)}\n"
        f"Аккаунтов: {len(active)}\nТекст:\n{data['final_text'][:200]}",
        reply_markup=kb,
    )
    await state.set_state(SenderStates.CONFIRM)


@router.callback_query(F.data == "send:go", SenderStates.CONFIRM)
async def do_send(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await _launch(callback.message, state, dry_run=bool(config.TEST_MODE))


@router.message(F.text == "🧪 Тест-прогон")
async def test_run(message: types.Message, state: FSMContext):
    await _launch(message, state, dry_run=True)


@router.callback_query(F.data == "send:cancel", SenderStates.CONFIRM)
async def cancel_send(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("❌ Отменено.", reply_markup=sender_kb)
    await state.set_state(None)


async def _launch(message: types.Message, state: FSMContext, dry_run: bool):
    data = await state.get_data()
    chats_path = data.get("chats_path")
    final_text = data.get("final_text")
    if not chats_path or not final_text:
        await message.answer("⚠️ Сначала загрузи список чатов и задай текст.")
        return

    raw = Path(chats_path).read_text(encoding="utf-8", errors="ignore")
    targets, dup, invalid = parse_lines_report(raw)
    if not targets:
        await message.answer("⚠️ Список чатов пуст (после чистки дедупа/невалида).")
        return

    chosen = data.get("active_accounts") or manager.all_names()
    chosen = [n for n in chosen if n in set(manager.all_names())]
    if not chosen:
        await message.answer("⚠️ Нет выбранных аккаунтов. Зайди в «👥 Мои аккаунты».")
        return

    # пары имя→объект чтобы прогресс не съезжал после фильтра неавторизованных
    pairs: list[tuple[str, UserSender]] = [(n, manager.get(n)) for n in chosen]

    if not dry_run:
        valid_pairs: list[tuple[str, UserSender]] = []
        for name, acc in pairs:
            try:
                await acc.connect()
                if await acc.is_authorized():
                    valid_pairs.append((name, acc))
            except Exception:
                pass
        if not valid_pairs:
            await message.answer("❌ Нет авторизованных аккаунтов среди выбранных.")
            return
        pairs = valid_pairs

    uid = message.from_user.id
    if uid in RUNNERS:
        await message.answer("⚠️ У тебя уже идёт кампания. Жми 🛑 Остановить чтобы прервать.")
        return
    stop_event = asyncio.Event()
    RUNNERS[uid] = stop_event

    campaign_id = cstore.create_campaign(uid, final_text, targets, dry_run=dry_run)
    cstore.mark_started(campaign_id)

    async def result_cb(target, status: str, error=None):
        try:
            cstore.record_target(campaign_id, target, status, error)
        except Exception:
            pass

    n = len(pairs)
    shares = [targets[i::n] for i in range(n)]

    tag = "🧪 ТЕСТ" if dry_run else "📤"
    total = len(targets)
    progress_msg = await message.answer(f"{tag} #{campaign_id}: подготовка... (стоп — 🛑 Остановить)")
    per_acc = {}
    agg = {"sent": 0, "skipped": 0, "failed": 0, "cancelled": 0}
    lock = asyncio.Lock()
    final_status = "failed"

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

        stats = await acc.send_to_chats(
            share, final_text, config.DELAY, cb,
            dry_run=dry_run, stop_event=stop_event, result_callback=result_cb,
        )
        async with lock:
            for k in agg:
                agg[k] += stats.get(k, 0)
            per_acc[acc_name] = len(share)
        await render(None, f"{acc_name}: финиш")

    try:
        await asyncio.gather(
            *(worker(acc, share, name) for (name, acc), share in zip(pairs, shares))
        )
        final_status = "cancelled" if agg.get("cancelled") else "completed"
        await message.answer(
            f"{tag} #{campaign_id} Готово!\n✅ Отправлено: {agg['sent']}\n"
            f"⏭ Пропущено: {agg['skipped']}\n❌ Ошибок: {agg['failed']}\n"
            f"🛑 Отменено: {agg.get('cancelled', 0)}\n"
            f"👥 Аккаунтов задействовано: {n}",
            reply_markup=sender_kb,
        )
    except Exception as e:
        final_status = "failed"
        try:
            await message.answer(f"❌ Кампания #{campaign_id} упала: {e}"[:400], reply_markup=sender_kb)
        except Exception:
            pass
    finally:
        RUNNERS.pop(uid, None)
        try:
            cstore.finish(campaign_id, final_status, sent=agg["sent"], skipped=agg["skipped"], failed=agg["failed"])
        except Exception:
            pass
    await state.set_state(None)


@router.message(F.text == "🛑 Остановить")
async def stop_campaign(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    ev = RUNNERS.get(uid)
    task = SCHEDULED.pop(uid, None)
    if task and not task.done():
        task.cancel()
        await message.answer("🛑 Запланированный запуск отменён.", reply_markup=sender_kb)
        return
    if ev is None:
        await message.answer("Нечего останавливать — активных кампаний нет.", reply_markup=sender_kb)
        return
    ev.set()
    await message.answer("🛑 Останавливаю… текущий чат допишется, остальные встанут.", reply_markup=sender_kb)


@router.message(F.text == "📜 История")
async def history(message: types.Message, state: FSMContext):
    rows = cstore.recent(message.from_user.id, limit=10)
    if not rows:
        await message.answer("📜 Пока пусто.", reply_markup=sender_kb)
        return
    lines = ["📜 Последние кампании:"]
    for r in rows:
        lines.append(
            f"#{r['id']} {r['status']}{' 🧪' if r['dry_run'] else ''} "
            f"всего {r['total']} ✅{r['sent']} ⏭{r['skipped']} ❌{r['failed']} ({r['created_at']})"
        )
    await message.answer("\n".join(lines), reply_markup=sender_kb)


@router.message(F.text == "🕒 Запланировать")
async def schedule_ask(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("chats_path") or not data.get("final_text"):
        await message.answer("⚠️ Сначала загрузи список чатов и задай текст.")
        return
    await message.answer(f"Через сколько минут запустить? (1…{config.MAX_SCHEDULE_MINUTES})")
    await state.set_state(SenderStates.SCHED_MINUTES)


@router.message(SenderStates.SCHED_MINUTES)
async def schedule_set(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    try:
        mins = int((message.text or "").strip())
    except Exception:
        await message.answer("❌ Пришли число минут.")
        return
    if not 1 <= mins <= config.MAX_SCHEDULE_MINUTES:
        await message.answer(f"❌ Диапазон 1…{config.MAX_SCHEDULE_MINUTES}.")
        return
    if uid in SCHEDULED and not SCHEDULED[uid].done():
        await message.answer("⚠️ У тебя уже есть запланированный запуск. Сначала 🛑 Остановить.")
        await state.set_state(None)
        return
    data = await state.get_data()
    if not data.get("chats_path") or not data.get("final_text"):
        await message.answer("⚠️ Список/текст слетели. Загрузи заново.")
        await state.set_state(None)
        return
    await state.set_state(None)

    async def _delayed():
        try:
            await asyncio.sleep(mins * 60)
        except asyncio.CancelledError:
            return
        # единственная кампания создаётся внутри _launch — пустых записей в истории нет
        await _launch(message, state, dry_run=bool(config.TEST_MODE))

    SCHEDULED[uid] = asyncio.create_task(_delayed())
    when = (datetime.now(timezone.utc) + timedelta(minutes=mins)).isoformat(timespec="minutes")
    await message.answer(
        f"🕒 Запланировал через {mins} мин ({when}).\n"
        "Запись в истории появится только при фактическом запуске. Отмена — 🛑 Остановить.",
        reply_markup=sender_kb,
    )


@router.message(F.text == "📊 Sender-статус")
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
    await message.answer("\n".join(lines), reply_markup=sender_kb)
