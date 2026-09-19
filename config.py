"""Конфигурация: .env + settings.json (API-ключи можно вводить прямо в боте)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
SETTINGS_FILE = BASE_DIR / "settings.json"
CAMPAIGNS_DB = DATA_DIR / "campaigns.sqlite3"


def _env(key: str, default: str = "") -> str:
    """Читаем переменную и сразу обрезаем пробелы/переводы строк."""
    return (os.getenv(key) or default).strip()


def _safe_int(value: object, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _load_settings() -> dict:
    try:
        with SETTINGS_FILE.open(encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


_settings = _load_settings()

# .env имеет приоритет над settings.json
API_ID = _safe_int(_env("API_ID") or _settings.get("API_ID"), 0)
API_HASH = _env("API_HASH") or str(_settings.get("API_HASH", ""))
BOT_TOKEN = _env("BOT_TOKEN")
# Пауза не должна быть <1: лимиты Telegram не обходятся.
DELAY = max(1, _safe_int(_env("DELAY", "10"), 10))
# Тест-режим: 1 = без реальной отправки.
TEST_MODE = _safe_int(_env("TEST_MODE", "0"), 0) == 1
MAX_SCHEDULE_MINUTES = max(1, _safe_int(_env("MAX_SCHEDULE_MINUTES", "1440"), 1440))

ALLOWED_RAW = _env("ALLOWED_USERS")
ALLOWED_USERS: set[int] = set()
for raw_id in ALLOWED_RAW.split(","):
    raw_id = raw_id.strip()
    if raw_id:
        try:
            ALLOWED_USERS.add(int(raw_id))
        except ValueError:
            pass

API_CONFIGURED = bool(API_ID and API_HASH)


def save_settings(data: dict) -> None:
    """Атомарно сохраняет ключи (temp+replace, chmod 600)."""
    _settings.update({k: v for k, v in data.items() if v not in (None, "")})
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="settings-", suffix=".json", dir=SETTINGS_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_settings, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, SETTINGS_FILE)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)

    global API_ID, API_HASH, API_CONFIGURED
    API_ID = _safe_int(_settings.get("API_ID"), 0)
    API_HASH = str(_settings.get("API_HASH", ""))
    API_CONFIGURED = bool(API_ID and API_HASH)


# --- Зеркало: атрибуция ---
def _read_version() -> str:
    try:
        return (BASE_DIR / "VERSION").read_text(encoding="utf-8").strip() or "dev"
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
