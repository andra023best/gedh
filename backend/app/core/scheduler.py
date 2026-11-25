"""Самодостатній щоденний планувальник (S-40, SQ-06).

Розклад GitHub Actions для нас ненадійний: запуск о 03:00 UTC 23.11 стартував лише близько 12:00,
а 25.11 не стартував зовсім. Тому сервер «доганяє» сам: після 03:00 UTC перший запит дня (відвідувач,
GitHub-будильник, адміністратор) запускає у фоні дві задачі:
- **daily** — завершення дозволів і нагадування (UC-19), строки зберігання (DG-23, DG-24),
  резервна копія (NFR-10);
- **import** — конектори, чий розклад (cron) припадає на сьогодні (UC-14, S-37).
Умовний UPDATE у `job_runs` гарантує один запуск на день, хоч би скільки викликів прийшло.
Render Free засинає після 15 хв простою, тож без жодного запиту за день задачі виконаються з першим запитом
наступного дня. Вони ідемпотентні й надолужують пропущене, а доступ і так перевіряється на кожному запиті.
"""

import json
import logging
import threading
import time as _time
import uuid
from datetime import UTC, date, datetime, time

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import new_session
from app.models.technical import JobRun

log = logging.getLogger("gedh.scheduler")
DUE_AT = time(3, 0)  # UTC: після цього часу задачі дня вважаються «на черзі»
CHECK_EVERY = 600  # с: як часто запит відвідувача може перевіряти, чи є що наздоганяти
JOBS = ("daily", "import")
_last_check = 0.0
_lock = threading.Lock()


def claim(db: Session, name: str, today: date, trigger: str) -> bool:
    """Атомарно «забирає» задачу на сьогодні; False — її вже взяв інший виклик."""
    row = db.execute(
        update(JobRun)
        .where(JobRun.name == name, or_(JobRun.last_date.is_(None), JobRun.last_date < today))
        .values(last_date=today, trigger=trigger, started_at=datetime.now(UTC), finished_at=None, result=None)
        .returning(JobRun.name)
    ).first()
    db.commit()
    return row is not None


def finish(name: str, result: dict) -> None:
    with new_session() as db:
        db.execute(
            update(JobRun)
            .where(JobRun.name == name)
            .values(
                finished_at=datetime.now(UTC),
                result=json.dumps(result, ensure_ascii=False, default=str)[:4000],
            )
        )
        db.commit()


def run_job(name: str, rid: str, scheduled: bool = True, now: datetime | None = None) -> dict:
    """Виконує задачу (без перевірки «чи вже була сьогодні» — це робить claim)."""
    from app.api.jobs import apply_retention, expire_grants  # локально: уникаємо циклу імпорту
    from app.core.backup import run_backup
    from app.ingestion.service import run_all
    from app.models.enums import RunTrigger

    now = now or datetime.now(UTC)
    try:
        if name == "daily":
            result = {
                "grants": expire_grants(rid, now),
                "retention": apply_retention(rid, now),
                "backup": _safe(lambda: run_backup(now.date())),
            }
        else:
            with new_session() as db:
                trigger = RunTrigger.SCHEDULE if scheduled else RunTrigger.MANUAL
                result = {"connectors": run_all(db, trigger, request_id=rid, today=now.date())}
    except Exception as exc:  # noqa: BLE001 — збій задачі не має валити сервер; причина — у job_runs
        log.exception("job %s failed", name)
        result = {"error": f"{type(exc).__name__}: {exc}"[:500]}
    finish(name, result)
    return result


def _safe(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — недоступність Neon API не скасовує інших щоденних задач
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:300]}


def run_due(trigger: str, now: datetime | None = None) -> list[str]:
    """Запускає (у поточному потоці) задачі дня, які ще ніхто не взяв. Повертає назви запущених."""
    now = now or datetime.now(UTC)
    if now.time() < DUE_AT:
        return []
    started = []
    for name in JOBS:
        with new_session() as db:
            if not claim(db, name, now.date(), trigger):
                continue
        started.append(name)
        run_job(name, f"{trigger}-{uuid.uuid4()}", scheduled=True, now=now)
    return started


def kick() -> None:
    """З middleware: не частіше ніж раз на 10 хв перевіряє, чи є що наздоганяти, — у фоновому потоці."""
    global _last_check
    if not get_settings().scheduler_enabled:
        return
    with _lock:
        if _time.monotonic() - _last_check < CHECK_EVERY:
            return
        _last_check = _time.monotonic()
    threading.Thread(target=_safe_run_due, name="gedh-catch-up", daemon=True).start()


def _safe_run_due() -> None:
    try:
        run_due("catch-up")
    except Exception:  # noqa: BLE001
        log.exception("catch-up failed")
