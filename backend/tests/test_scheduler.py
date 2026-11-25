"""S-40: сервер сам «доганяє» щоденні задачі (SQ-06) і робить резервні копії в Neon (SQ-07, NFR-10)."""

import os
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.core import backup, scheduler
from app.core.config import get_settings
from app.core.db import new_session
from app.main import app
from app.models.enums import Role
from app.models.technical import JobRun
from tests.test_identity import auth, login, make_user

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="БД не налаштовано")


def reset_jobs(day: date | None) -> None:
    with new_session() as db:
        db.execute(
            update(JobRun).values(last_date=day, trigger=None, started_at=None, finished_at=None, result=None)
        )
        db.commit()


def test_catch_up_runs_each_job_once_per_day_after_03_utc(monkeypatch):
    ran = []
    monkeypatch.setattr(scheduler, "run_job", lambda name, rid, scheduled=True, now=None: ran.append(name))
    today = datetime(2030, 1, 15, tzinfo=UTC)
    reset_jobs(today.date() - timedelta(days=1))
    assert scheduler.run_due("catch-up", now=today.replace(hour=2, minute=59)) == []  # ще рано
    assert scheduler.run_due("catch-up", now=today.replace(hour=3, minute=1)) == ["daily", "import"]
    assert scheduler.run_due("github", now=today.replace(hour=9)) == []  # уже виконано сьогодні
    assert ran == ["daily", "import"]
    with new_session() as db:
        assert db.get(JobRun, "daily").trigger == "catch-up"
    reset_jobs(None)


def test_daily_job_records_result(monkeypatch):
    monkeypatch.setattr(backup, "run_backup", lambda today, http=None: {"status": "skipped"})
    result = scheduler.run_job("daily", "test-daily")
    assert {"grants", "retention", "backup"} <= set(result)
    with new_session() as db:
        j = db.get(JobRun, "daily")
        assert j.finished_at is not None and '"expired"' in j.result


def test_backup_creates_snapshot_and_keeps_7_days(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "neon_api_key", None)
    assert backup.run_backup(date(2030, 1, 15))["status"] == "skipped"
    monkeypatch.setattr(s, "neon_api_key", "k")
    monkeypatch.setattr(s, "neon_project_id", "p")
    calls = []
    branches = [
        {"id": "main", "name": "main"},
        {"id": "b1", "name": "backup-2030-01-08"},  # 7 днів тому — старше вікна
        {"id": "b2", "name": "backup-2030-01-09"},  # ще в межах 7 останніх
        {"id": "b3", "name": "backup-junk"},
    ]

    def http(method, path, body):
        calls.append((method, path, body))
        return {"branches": branches} if method == "GET" else {}

    r = backup.run_backup(date(2030, 1, 15), http)
    assert r == {
        "status": "ok",
        "snapshot": "backup-2030-01-15",
        "created": True,
        "deleted": ["backup-2030-01-08"],
    }
    assert ("POST", "/projects/p/branches", {"branch": {"name": "backup-2030-01-15"}}) in calls
    assert ("DELETE", "/projects/p/branches/b1", None) in calls
    assert not any(c[0] == "DELETE" and c[1].endswith(("/main", "/b2", "/b3")) for c in calls)
    branches.append({"id": "b4", "name": "backup-2030-01-15"})
    calls.clear()
    assert backup.run_backup(date(2030, 1, 15), http)["created"] is False  # ідемпотентно
    assert not any(c[0] == "POST" for c in calls)


def test_admin_sees_and_runs_jobs(monkeypatch):
    c = TestClient(app)
    assert c.get("/api/admin/jobs", headers=auth(login(c, make_user().email))).status_code == 403
    admin = auth(login(c, make_user(Role.ADMIN).email))
    jobs = c.get("/api/admin/jobs", headers=admin).json()
    assert {j["name"] for j in jobs} == {"daily", "import"} and all(j["title"] for j in jobs)
    ran = []
    monkeypatch.setattr(
        scheduler, "run_job", lambda name, rid, scheduled=True, now=None: ran.append((name, scheduled))
    )
    assert c.post("/api/admin/jobs/import/run", headers=admin).status_code == 202
    assert ran == [("import", False)]  # вручну — усі увімкнені конектори, а не лише «за розкладом»
    assert c.post("/api/admin/jobs/nope/run", headers=admin).status_code == 404
    assert c.post("/api/internal/jobs/daily").json()["detail"] == "JOB_TOKEN_INVALID"
