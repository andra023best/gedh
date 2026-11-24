"""Обмеження частоти запитів (NFR-18) — у пам'яті процесу.

На Render Free працює один процес Uvicorn, тому лічильника в пам'яті достатньо. Ключ — IP клієнта
з `x-vercel-forwarded-for` (S-07). Лічильник невдалих входів окремо зберігається в БД (User.failedLoginCount).
"""

import time
from collections import defaultdict, deque

from fastapi import Request

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.proxy_guard import client_ip

LIMITS = {
    "login": (10, 60),  # NFR-18: 10 спроб входу за хвилину з однієї IP
    "register": (5, 60),
    "mail": (5, 60),  # повторні листи, уточнення
    "api_key": (60, 60),  # NFR-18: 60 запитів за хвилину на API-ключ
}

_hits: dict[tuple[str, str], deque] = defaultdict(deque)


def check(request: Request, bucket: str) -> None:
    if not get_settings().rate_limit_enabled:
        return
    limit, window = LIMITS[bucket]
    key = (bucket, client_ip(request) or "unknown")
    now = time.monotonic()
    hits = _hits[key]
    while hits and now - hits[0] > window:
        hits.popleft()
    if len(hits) >= limit:
        raise ApiError(429, "RATE_LIMITED", retry_after=int(window - (now - hits[0])) + 1)
    hits.append(now)


def check_key(prefix: str) -> None:
    """NFR-18: ліміт на API-ключ (а не на IP — клієнт API може працювати з кількох адрес)."""
    if not get_settings().rate_limit_enabled:
        return
    limit, window = LIMITS["api_key"]
    now = time.monotonic()
    hits = _hits[("api_key", prefix)]
    while hits and now - hits[0] > window:
        hits.popleft()
    if len(hits) >= limit:
        raise ApiError(429, "RATE_LIMITED", retry_after=int(window - (now - hits[0])) + 1)
    hits.append(now)


def reset() -> None:
    _hits.clear()
