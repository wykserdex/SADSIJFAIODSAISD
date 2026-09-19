"""Telethon-клиент: вход в аккаунт + рассылка по списку чатов."""
import asyncio
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    SessionPasswordNeededError,
    ChatWriteForbiddenError,
    UserIsBlockedError,
    ChannelPrivateError,
    UserBannedInChannelError,
    UsernameNotOccupiedError,
    PeerIdInvalidError,
)

import config

SESSIONS_DIR = Path(__file__).parent / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)


def parse_chat_target(line: str):
    """Разбирает одну строку списка в (kind, value)."""
    line = line.strip()
    if not line:
        return None
    if "t.me/+" in line or "telegram.me/+" in line:
        return ("invite", line.split("+", 1)[1])
    if line.startswith("@"):
        return ("username", line[1:])
    if "t.me/" in line:
        uname = line.split("t.me/", 1)[1].split("/")[0].split("?")[0]
        if uname:
            return ("username", uname)
    if line.lstrip("-").isdigit():
        return ("id", int(line))
    return ("username", line)


def parse_lines_to_targets(text: str):
    """Парсит весь текст списка в список (kind, value)."""
    targets = []
    for line in text.splitlines():
        t = parse_chat_target(line)
        if t:
            targets.append(t)
    return targets


class UserSender:
    def __init__(self, session_name: str = "user"):
        self.session_path = str(SESSIONS_DIR / session_name)
        self.client = None
        self.phone_code_hash = None

    def _ensure_client(self):
        # создаём клиент в момент подключения, чтобы брать свежие API_ID/API_HASH из config
        if self.client is None:
            self.client = TelegramClient(self.session_path, config.API_ID, config.API_HASH)
        return self.client

    # ---------- авторизация ----------
    async def connect(self):
        self._ensure_client()
        await self.client.connect()

    async def disconnect(self):
        await self.client.disconnect()

    async def is_authorized(self) -> bool:
        return await self.client.is_user_authorized()

    async def send_code(self, phone: str):
        result = await self.client.send_code_request(phone)
        self.phone_code_hash = result.phone_code_hash
        return self.phone_code_hash

    async def sign_in(self, phone: str, code: str):
        try:
            await self.client.sign_in(phone, code, phone_code_hash=self.phone_code_hash)
            return "ok"
        except SessionPasswordNeededError:
            return "2fa"  # требуется пароль двухфакторной аутентификации

    async def sign_in_password(self, password: str):
        await self.client.sign_in(password=password)
        return "ok"

    # ---------- парсинг списка ----------
    def parse_chat_target(self, line):
        return parse_chat_target(line)

    def parse_lines_to_targets(self, text):
        return parse_lines_to_targets(text)

    # ---------- отправка (только своя сетка: чужие чаты уходят в skipped) ----------
    async def _resolve(self, kind, value):
        if kind == "invite":
            # invite-ссылки: не джойнимся в чужие чаты автоматом.
            # Для своей сетки добавь бота/акк заранее руками, сюда кидай username/id.
            return await self.client.get_entity("https://t.me/+" + value)
        return await self.client.get_entity(value)

    async def _safe_send(self, entity, text):
        await self.client.send_message(entity, text)

    def _label(self, kind, value) -> str:
        if kind == "invite":
            return f"invite:+{str(value)[:10]}…"
        if kind == "id":
            return f"id:{value}"
        return "@" + str(value)

    async def send_to_chats(self, targets, text, delay, progress=None, dry_run=False):
        stats = {"sent": 0, "skipped": 0, "failed": 0}
        total = len(targets)
        for idx, (kind, value) in enumerate(targets, 1):
            current = self._label(kind, value)
            # показываем, какой чат обрабатываем прямо сейчас
            if progress:
                await progress(idx - 1, total, f"обрабатываю {current}", current)
            # ---- ТЕСТ-РЕЖИМ: без реальной отправки ----
            if dry_run:
                if progress:
                    await progress(idx, total, f"ТЕСТ: {current} (без отправки)", current)
                stats["sent"] += 1
                await asyncio.sleep(min(delay, 1))
                continue
            try:
                entity = await self._resolve(kind, value)
                await self._safe_send(entity, text)
                stats["sent"] += 1
            except FloodWaitError as e:
                if progress:
                    await progress(idx, total, f"⏳ FloodWait {e.seconds}s — жду", current)
                await asyncio.sleep(e.seconds)
                try:
                    entity = await self._resolve(kind, value)
                    await self._safe_send(entity, text)
                    stats["sent"] += 1
                except Exception:
                    stats["failed"] += 1
            except (
                ChatWriteForbiddenError,
                UserIsBlockedError,
                ChannelPrivateError,
                UserBannedInChannelError,
                UsernameNotOccupiedError,
                PeerIdInvalidError,
            ):
                stats["skipped"] += 1
            except Exception:
                stats["failed"] += 1

            if progress:
                await progress(idx, total, None, current)
            await asyncio.sleep(delay)
        return stats
