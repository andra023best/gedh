"""Щоденна резервна копія БД (NFR-10, SQ-07, S-40) — гілка-знімок у Neon через Neon API.

Гілка Neon — миттєвий знімок стану бази (copy-on-write): копія не навантажує сервер і не виходить за межі ЄС
(DG-27). Зберігаємо 7 останніх (`backup-РРРР-ММ-ДД`), старші видаляємо (безкоштовний тариф — до 10 гілок).
Відновлення: у консолі Neon «Restore» основної гілки з потрібного знімка або перемкнути DATABASE_URL
на гілку — хвилини, що вкладається в NFR-10 (≤ 4 год). Ключ — лише в змінних середовища Render (NFR-20).
"""

import json
import urllib.request
from collections.abc import Callable
from datetime import date, timedelta

from app.core.config import get_settings

API = "https://console.neon.tech/api/v2"
KEEP_DAYS = 7
PREFIX = "backup-"

Http = Callable[[str, str, dict | None], dict]


def neon_http(method: str, path: str, body: dict | None = None) -> dict:
    s = get_settings()
    req = urllib.request.Request(  # noqa: S310 — фіксована https-адреса Neon API
        f"{API}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {s.neon_api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        raw = resp.read()
    return json.loads(raw) if raw else {}


def run_backup(today: date, http: Http = neon_http) -> dict:
    """Створює знімок за сьогодні (якщо ще немає) і видаляє знімки, старші за 7 днів. Ідемпотентно."""
    s = get_settings()
    if not (s.neon_api_key and s.neon_project_id):
        return {"status": "skipped", "reason": "NEON_API_KEY / NEON_PROJECT_ID не задано"}
    base = f"/projects/{s.neon_project_id}/branches"
    branches = http("GET", base, None).get("branches", [])
    name = f"{PREFIX}{today.isoformat()}"
    created = False
    if not any(b.get("name") == name for b in branches):
        http("POST", base, {"branch": {"name": name}})  # від основної гілки, без обчислювального вузла
        created = True
    deleted = []
    edge = today - timedelta(days=KEEP_DAYS - 1)
    for b in branches:
        n = b.get("name", "")
        if not n.startswith(PREFIX):
            continue
        try:
            day = date.fromisoformat(n[len(PREFIX) :])
        except ValueError:
            continue
        if day < edge:
            http("DELETE", f"{base}/{b['id']}", None)
            deleted.append(n)
    return {"status": "ok", "snapshot": name, "created": created, "deleted": sorted(deleted)}
