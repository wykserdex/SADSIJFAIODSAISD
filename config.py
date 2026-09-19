"""Конфигурация: .env + settings.json (API-ключи можно вводить прямо в боте)."""
import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

SETTINGS_FILE = Path(__file__).parent / "settings.json"


def _env(key, default=""):
    """Читаем переменную и сразу обрезаем пробелы/переводы строк (частая причина ошибок)."""
    return (os.getenv(key) or default).strip()


def _load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


_settings = _load_settings()

# .env имеет приоритет над settings.json
API_ID = int(_env("API_ID") or _settings.get("API_ID") or "0")
API_HASH = _env("API_HASH") or _settings.get("API_HASH", "")
BOT_TOKEN = _env("BOT_TOKEN")
# Пауза между сообщениями (сек). Чем больше — тем безопаснее для аккаунта.
DELAY = int(_env("DELAY", "10") or "10")
# Тест-режим: 1 = без реальной отправки (имитация, для проверки флоу).
TEST_MODE = int(_env("TEST_MODE", "0") or "0")
# Whitelist: список user_id через запятую. Пусто = доступ у всех (небезопасно!).
ALLOWED_RAW = _env("ALLOWED_USERS")
def _parse_allowed(raw: str):
    out = set()
    for x in raw.split(","):
        x = x.strip()
        if not x:
            continue
        try:
            out.add(int(x))
        except ValueError:
            continue
    return out
ALLOWED_USERS = _parse_allowed(ALLOWED_RAW)

API_CONFIGURED = bool(API_ID) and bool(API_HASH)


def save_settings(data: dict):
    """Сохраняем API-ключи в settings.json и сразу обновляем переменные в рантайме."""
    _settings.update({k: v for k, v in data.items() if v not in (None, "")})
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(_settings, f, indent=2, ensure_ascii=False)
    global API_ID, API_HASH, API_CONFIGURED
    API_ID = int(_settings.get("API_ID", "0") or "0")
    API_HASH = _settings.get("API_HASH", "")
API_CONFIGURED = bool(API_ID) and bool(API_HASH)

# --- Зеркало: атрибуция ---
def _read_version() -> str:
    try:
        return (Path(__file__).parent / "VERSION").read_text(encoding="utf-8").strip() or "dev"
    except Exception:
        return "dev"

VERSION = _read_version()
# Откуда взят проект (не менять в форках — это исходник)
SOURCE_URL = _env("SOURCE_URL", "https://github.com/wykserdex/SADSIJFAIODSAISD") or "https://github.com/wykserdex/SADSIJFAIODSAISD"
# Чей форк: username/ссылка того, у кого взял зеркало. У исходника — пусто.
FORK_OF = _env("FORK_OF", "")
# Имя этого зеркала и владелец (показывается в info)
INSTANCE_NAME = _env("INSTANCE_NAME", "мой мультитул") or "мой мультитул"
OWNER = _env("OWNER", "")


if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN пустой — проверь файл .env (имя должно быть ровно .env, без .txt)")
