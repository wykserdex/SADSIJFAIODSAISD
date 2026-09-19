"""Пассивные OSINT-инструменты для публичных адресов.

Здесь намеренно нет порт-сканирования, перебора учётных данных, эксплуатации
или обхода ограничений. Инструменты делают только DNS-запросы и обычные GET/HEAD
к указанному публичному HTTP(S)-адресу.
"""
from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx


class UnsafeTarget(ValueError):
    """Цель указывает на локальную/приватную сеть или неверный URL."""



def _resolved_ips(hostname: str) -> set[str]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeTarget(f"не удалось разрешить домен: {exc}") from exc
    ips = {str(item[4][0]) for item in infos}
    if not ips:
        raise UnsafeTarget("домен не вернул IP-адресов")
    for raw_ip in ips:
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError as exc:
            raise UnsafeTarget("получен некорректный IP-адрес") from exc
        if any((ip.is_private, ip.is_loopback, ip.is_link_local, ip.is_multicast, ip.is_reserved, ip.is_unspecified)):
            raise UnsafeTarget("локальные и приватные адреса запрещены")
    return ips



def normalize_domain(value: str) -> str:
    value = value.strip()
    if "://" in value:
        parsed = urlparse(value)
        hostname = parsed.hostname or ""
    else:
        hostname = value.split("/", 1)[0]
    hostname = hostname.strip().lower().rstrip(".")
    if not hostname or any(ch.isspace() for ch in hostname):
        raise UnsafeTarget("нужен домен или URL")
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise UnsafeTarget("локальные домены запрещены")
    # Проверяем публичность, но сам результат DNS не используем для сканирования.
    ips = _resolved_ips(hostname)
    return f"{hostname} ({', '.join(sorted(ips))})"



def normalize_public_url(value: str) -> str:
    value = value.strip()
    if not value:
        raise UnsafeTarget("URL пустой")
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeTarget("разрешены только http:// и https://")
    if parsed.username or parsed.password or parsed.port:
        raise UnsafeTarget("логины, пароли и нестандартные порты запрещены")
    if parsed.hostname in {"localhost", "localhost.localdomain"} or parsed.hostname.endswith(".local"):
        raise UnsafeTarget("локальные домены запрещены")
    _resolved_ips(parsed.hostname)
    return parsed.geturl()


async def dns_lookup(value: str) -> str:
    """Возвращает домен и публичные A/AAAA-адреса без проверки портов."""
    value = value.strip()
    if "://" in value:
        value = urlparse(value).hostname or ""
    if not value:
        raise UnsafeTarget("домен пустой")
    hostname = value.split("/", 1)[0].lower().rstrip(".")
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise UnsafeTarget("локальные домены запрещены")
    ips = await asyncio.to_thread(_resolved_ips, hostname)
    return f"🌐 DNS для {hostname}\n" + "\n".join(f"• {ip}" for ip in sorted(ips))


async def fetch_headers(value: str) -> str:
    url = normalize_public_url(value)
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        async with client.stream(
            "GET", url, headers={"User-Agent": "SADSIJFAIODSAISD-OSINT/1.0"}
        ) as response:
            status_code = response.status_code
            headers = response.headers
    lines = [
        f"🧾 HTTP-заголовки: {url}",
        f"Статус: {status_code}",
        f"Тип: {headers.get('content-type', '—')}",
        f"Длина: {headers.get('content-length', '—')}",
    ]
    for key in (
        "server",
        "date",
        "cache-control",
        "strict-transport-security",
        "content-security-policy",
        "x-frame-options",
        "x-content-type-options",
        "location",
    ):
        if key in headers:
            lines.append(f"{key}: {headers[key][:300]}")
    return "\n".join(lines)


class _MetaParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title: list[str] = []
        self.description = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = {str(k).lower(): str(v or "") for k, v in attrs}
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "meta" and attrs_dict.get("name", "").lower() == "description":
            self.description = attrs_dict.get("content", "")[:1000]

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and "".join(self.title).__len__() < 500:
            self.title.append(data)


async def url_metadata(value: str) -> str:
    url = normalize_public_url(value)
    async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
        async with client.stream(
            "GET", url, headers={"User-Agent": "SADSIJFAIODSAISD-OSINT/1.0"}
        ) as response:
            content_type = response.headers.get("content-type", "")
            raw = b""
            if "html" in content_type.lower() or not content_type:
                async for chunk in response.aiter_bytes():
                    raw += chunk
                    if len(raw) >= 256_000:
                        break
    parser = _MetaParser()
    try:
        parser.feed(raw[:256_000].decode("utf-8", errors="replace"))
    except Exception:
        pass
    title = html.unescape(re.sub(r"\s+", " ", "".join(parser.title))).strip() or "—"
    description = html.unescape(re.sub(r"\s+", " ", parser.description)).strip() or "—"
    return (
        f"🔗 Метаданные URL: {url}\n"
        f"Заголовок: {title[:500]}\n"
        f"Описание: {description[:1000]}\n"
        f"Размер прочитанного HTML: {len(raw)} байт"
    )
