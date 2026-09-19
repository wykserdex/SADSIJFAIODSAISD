"""OSINT-safe категория: только свои домены. CT (crt.sh) + DNS. Без людей."""
import asyncio
import json
import re
import socket
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

INFO = {
    "id": "osint",
    "title": "🔍 OSINT-safe",
    "desc": "Свои домены: CT-сертификаты + DNS. Людей/почты/ники не ищем.",
}

router = Router()

_DOMAIN_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))*\.[a-z]{2,}$")


class OsintStates(StatesGroup):
    WAIT_DOMAIN = State()


def clean_domain(raw: str) -> str | None:
    """Строго домен. Email/телефон/ник/URL с путем — reject (возвращаем None)."""
    if not raw:
        return None
    s = raw.strip().lower()
    if not s or len(s) > 253:
        return None
    if "@" in s:  # email — не принимаем
        return None
    if s.startswith("+") or (s.replace(" ", "").replace("-", "").replace("(", "").replace(")", "").isdigit()):
        return None  # телефон — не принимаем
    # отрезаем схему/путь если вставили URL
    if "://" in s:
        try:
            host = urllib.parse.urlsplit(s if "://" in s else "https://" + s).hostname or ""
        except Exception:
            return None
        s = host
    s = s.strip().strip(".")
    # только хост без пути/порта/параметров
    s = s.split("/")[0].split("?")[0].split("#")[0]
    if ":" in s:  # порт — отрезаем, но только если остаток валиден
        s = s.split(":")[0]
    if not _DOMAIN_RE.match(s):
        return None
    if s.startswith("*."):
        s = s[2:]
    return s or None


def summarize_crt(items: list, now=None) -> dict:
    """Агрегат по сырому JSON crt.sh: total/names/issuers/expired. Сырьё не тащим в вывод целиком."""
    now = now or datetime.now(timezone.utc)
    names: set[str] = set()
    issuers: set[str] = set()
    expired = 0
    total = len(items)
    for it in items:
        nv = (it.get("name_value") or "").strip()
        for part in re.split(r"[\n,]+", nv):
            p = part.strip().lower().strip("*.")
            if p:
                names.add(p)
        iss = (it.get("issuer_name") or "").strip()
        if iss:
            issuers.add(iss[:120])
        exp = it.get("not_after") or it.get("notAfter") or ""
        if exp:
            try:
                # crt.sh: "2025-01-02T00:00:00" или с Z
                dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt < now:
                    expired += 1
            except Exception:
                pass
    return {
        "total": total,
        "names": sorted(names)[:20],
        "names_total": len(names),
        "issuers": sorted(issuers)[:10],
        "expired": expired,
    }


def _fetch_crt_sync(domain: str, timeout: int = 15) -> list:
    q = urllib.parse.quote(f"%.{domain}", safe="")
    url = f"https://crt.sh/?q={q}&output=json"
    req = urllib.request.Request(url, headers={"User-Agent": "multitool-osint-safe/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _resolve_dns_sync(domain: str, timeout: int = 10) -> dict:
    socket.setdefaulttimeout(timeout)
    try:
        name, aliases, ips = socket.gethostbyname_ex(domain)
        return {"host": name, "ips": ips[:10], "aliases": aliases[:5]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"[:200]}


async def check_domain(domain: str) -> dict:
    crt = await asyncio.to_thread(_fetch_crt_sync, domain)
    dns = await asyncio.to_thread(_resolve_dns_sync, domain)
    return {"summary": summarize_crt(crt), "dns": dns}


def _menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Проверить домен", callback_data="osint:check")],
        [InlineKeyboardButton(text="ℹ️ Что умею", callback_data="osint:about")],
    ])


async def enter(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🔍 OSINT-safe — только свои домены.\n"
        "Умею: CT (crt.sh) + DNS. Не ищу людей, почты, ники, телефоны.",
        reply_markup=_menu(),
    )


@router.callback_query(F.data == "cat_enter:osint")
async def on_enter(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await enter(callback.message, state)


@router.callback_query(F.data == "osint:about")
async def on_about(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "🔍 Проверяю домен:\n"
        "• crt.sh — сколько сертов, имена, издатели, просрочки\n"
        "• DNS — A-записи\n\n"
        "Вставь именно домен: example.com\n"
        "Email / @nick / телефон — отклоню, это не мой профиль."
    )


@router.callback_query(F.data == "osint:check")
async def on_check(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(OsintStates.WAIT_DOMAIN)
    await callback.message.answer("Пришли домен (напр. example.com):")


@router.message(OsintStates.WAIT_DOMAIN)
async def on_domain(message: types.Message, state: FSMContext):
    d = clean_domain(message.text or "")
    if not d:
        await message.answer("❌ Нужен именно домен (example.com). Почты/ники/телефоны не принимаю. Попробуй ещё:")
        return
    await state.clear()
    wait = await message.answer(f"🔍 Смотрю {d} … (CT + DNS, до ~20 сек)")
    try:
        res = await asyncio.wait_for(check_domain(d), timeout=30)
    except asyncio.TimeoutError:
        await wait.edit_text("⏳ Долго отвечает crt.sh/DNS. Попробуй позже.")
        return
    except Exception as e:
        await wait.edit_text(f"❌ Ошибка: {e}"[:400])
        return
    s = res["summary"]
    dns = res["dns"]
    lines = [
        f"🔍 {d}",
        f"CT: сертов {s['total']}, имён {s['names_total']}, просрочено {s['expired']}",
    ]
    if s["names"]:
        lines.append("Имена: " + ", ".join(s["names"][:10]))
    if s["issuers"]:
        lines.append("Издатели: " + ", ".join(s["issuers"][:5]))
    if "error" in dns:
        lines.append(f"DNS: ❌ {dns['error']}")
    else:
        ips = ", ".join(dns.get("ips", [])[:5]) or "—"
        lines.append(f"DNS: {ips}")
    await wait.edit_text("\n".join(lines)[:3500], reply_markup=_menu())
