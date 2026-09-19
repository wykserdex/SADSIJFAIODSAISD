"""Telethon-клиент: авторизация и отправка по согласованному списку чатов.

Модуль намеренно не пытается обходить FloodWait и не вступает автоматически
в приватные чаты по invite-ссылкам. Список адресатов должен использоваться
только для своих чатов или получателей, давших согласие.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Awaitable, Callable

from telethon import TelegramClient
from telethon.errors import (
    ChannelPrivateError,
    ChatWriteForbiddenError,
    FloodWaitError,
    PeerIdInvalidError,
    SessionPasswordNeededError,
    UserBannedInChannelError,
    UserIsBlockedError,
    UsernameNotOccupiedError,
)

import config

SESSIONS_DIR = Path(__file__).parent / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_INVITE_RE = re.compile(
    r"^(?:https?://)?(?:t\.me|telegram\.me)/\+([A-Za-z0-9_-]+)(?:\?.*)?$",
    re.IGNORECASE,
)

ResultCallback = Callable[
    [tuple[str, str | int], str, str | None], Awaitable[None]
]
ProgressCallback = Callable[[int, int, str | None, str], Awaitable[None]]


def _clean_line(line: str) -> str:
    return line.strip().lstrip("\ufeff")


def parse_chat_target(line: str) -> tuple[str, str | int] | None:
    """Разбирает строку в ``(kind, value)`` или возвращает ``None``.

    Поддерживаются только понятные Telegram-форматы: username, публичная
    ссылка, invite-ссылка и числовой chat id. Комментарии можно начинать с #.
    """
    line = _clean_line(line)
    if not line or line.startswith("#"):
        return None

    invite = _INVITE_RE.match(line)
    if invite:
        return ("invite", invite.group(1))

    if line.startswith("@"):
        username = line[1:]
        return ("username", username) if _USERNAME_RE.fullmatch(username) else None

    if line.lstrip("-").isdigit():
        return ("id", int(line))

    # Публичная ссылка вида https://t.me/name или https://telegram.me/name.
    match = re.match(
        r"^(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{1,32})(?:\?.*)?$",
        line,
        re.IGNORECASE,
    )
    if match:
        return ("username", match.group(1))

    if _USERNAME_RE.fullmatch(line):
        return ("username", line)
    return None


def parse_lines_report(text: str) -> tuple[list[tuple[str, str | int]], int, int]:
    """Возвращает ``(targets, duplicate_count, invalid_count)``.

    Порядок сохраняется, дубликаты удаляются. Это предотвращает повторную
    обработку одной и той же цели при случайном повторе строки в файле.
    """
    targets: list[tuple[str, str | int]] = []
    seen: set[tuple[str, str | int]] = set()
    duplicates = 0
    invalid = 0
    for raw_line in text.splitlines():
        line = _clean_line(raw_line)
        if not line or line.startswith("#"):
            continue
        target = parse_chat_target(line)
        if target is None:
            invalid += 1
            continue
        if target in seen:
            duplicates += 1
            continue
        seen.add(target)
        targets.append(target)
    return targets, duplicates, invalid


def parse_lines_to_targets(text: str) -> list[tuple[str, str | int]]:
    """Совместимый простой API: возвращает только нормализованный список."""
    return parse_lines_report(text)[0]


async def _sleep_or_stop(seconds: float, stop_event: asyncio.Event | None) -> bool:
    """Спит заданное время; возвращает True, если пришла остановка."""
    if seconds <= 0:
        return bool(stop_event and stop_event.is_set())
    if stop_event is None:
        await asyncio.sleep(seconds)
        return False
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        return False
    return True


class UserSender:
    def __init__(self, session_name: str = "user"):
        self.session_path = str(SESSIONS_DIR / session_name)
        self.client: TelegramClient | None = None
        self.phone_code_hash: str | None = None

    def _ensure_client(self) -> TelegramClient:
        # Клиент создаётся при подключении, чтобы брать свежие настройки.
        if self.client is None:
            self.client = TelegramClient(self.session_path, config.API_ID, config.API_HASH)
        return self.client

    # ---------- авторизация ----------
    async def connect(self) -> None:
        await self._ensure_client().connect()

    async def disconnect(self) -> None:
        if self.client is not None:
            await self.client.disconnect()

    async def is_authorized(self) -> bool:
        return bool(self.client and await self.client.is_user_authorized())

    async def send_code(self, phone: str) -> str:
        result = await self._ensure_client().send_code_request(phone)
        self.phone_code_hash = result.phone_code_hash
        return self.phone_code_hash

    async def sign_in(self, phone: str, code: str) -> str:
        try:
            await self._ensure_client().sign_in(
                phone, code, phone_code_hash=self.phone_code_hash
            )
            return "ok"
        except SessionPasswordNeededError:
            return "2fa"

    async def sign_in_password(self, password: str) -> str:
        await self._ensure_client().sign_in(password=password)
        return "ok"

    # ---------- парсинг списка ----------
    def parse_chat_target(self, line: str):
        return parse_chat_target(line)

    def parse_lines_to_targets(self, text: str):
        return parse_lines_to_targets(text)

    # ---------- отправка ----------
    async def _resolve(self, kind: str, value: str | int):
        if kind == "invite":
            # Не вступаем автоматически в приватные чаты: оператор должен
            # заранее иметь доступ к ним и сам подтвердить разрешение.
            return await self._ensure_client().get_entity("https://t.me/+" + str(value))
        return await self._ensure_client().get_entity(value)

    async def _safe_send(self, entity, text: str) -> None:
        await self._ensure_client().send_message(entity, text)

    def _label(self, kind: str, value: str | int) -> str:
        if kind == "invite":
            return f"invite:+{str(value)[:10]}…"
        if kind == "id":
            return f"id:{value}"
        return "@" + str(value)

    async def send_to_chats(
        self,
        targets: list[tuple[str, str | int]],
        text: str,
        delay: float,
        progress: ProgressCallback | None = None,
        *,
        dry_run: bool = False,
        stop_event: asyncio.Event | None = None,
        result_callback: ResultCallback | None = None,
    ) -> dict[str, int]:
        """Отправляет по одному сообщению на цель с контролем остановки.

        ``result_callback`` получает ``(target, status, error)`` для истории.
        FloodWait не обходится: бот ждёт ровно указанный Telegram срок, а
        оператор может остановить кампанию во время ожидания.
        """
        stats = {"sent": 0, "skipped": 0, "failed": 0, "cancelled": 0}
        total = len(targets)

        async def emit(target, status: str, error: str | None = None) -> None:
            if result_callback:
                await result_callback(target, status, error)

        for idx, (kind, value) in enumerate(targets, 1):
            if stop_event and stop_event.is_set():
                stats["cancelled"] += total - idx + 1
                break

            target = (kind, value)
            current = self._label(kind, value)
            if progress:
                await progress(idx - 1, total, f"обрабатываю {current}", current)

            if dry_run:
                if progress:
                    await progress(idx, total, f"ТЕСТ: {current} (без отправки)", current)
                await emit(target, "dry_run")
                stats["sent"] += 1
                if await _sleep_or_stop(min(delay, 1), stop_event):
                    stats["cancelled"] += total - idx
                    break
                continue

            status = "failed"
            error: str | None = None
            try:
                entity = await self._resolve(kind, value)
                await self._safe_send(entity, text)
                status = "sent"
                stats["sent"] += 1
            except FloodWaitError as exc:
                if progress:
                    await progress(idx - 1, total, f"⏳ FloodWait {exc.seconds}s — жду", current)
                interrupted = await _sleep_or_stop(exc.seconds, stop_event)
                if interrupted:
                    stats["cancelled"] += total - idx + 1
                    await emit(target, "cancelled", "остановлено во время FloodWait")
                    break
                try:
                    entity = await self._resolve(kind, value)
                    await self._safe_send(entity, text)
                    status = "sent"
                    stats["sent"] += 1
                except Exception as retry_exc:  # повторная ошибка — в историю
                    error = type(retry_exc).__name__
                    stats["failed"] += 1
            except (
                ChatWriteForbiddenError,
                UserIsBlockedError,
                ChannelPrivateError,
                UserBannedInChannelError,
                UsernameNotOccupiedError,
                PeerIdInvalidError,
            ) as exc:
                status = "skipped"
                error = type(exc).__name__
                stats["skipped"] += 1
            except Exception as exc:
                error = type(exc).__name__
                stats["failed"] += 1

            await emit(target, status, error)
            if progress:
                await progress(idx, total, None, current)
            if await _sleep_or_stop(delay, stop_event):
                stats["cancelled"] += total - idx
                break

        return stats
