# ruff: noqa: F811, S311 — фікстура unemp з test_e6b; random — лише для тестових значень
"""Етап Е6в: конектори й довідники (UC-20; FR-17, FR-43), API-ключі (FR-12, FR-37, NFR-18),
гість лише з «Про платформу» (S-36). S-37."""

import json
import os
import random
import string
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import ratelimit
from app.core.config import get_settings
from app.core.db import new_session
from app.ingestion import service
from app.main import app
from app.models.audit import AuditEvent
from app.models.enums import AuditAction, Role, RunTrigger
from app.models.exchange import DataExport
from app.models.ingestion import Connector
from app.models.reference import DataCategory
from app.seed.__main__ import sid
from tests.test_e6b import unemp  # noqa: F401 — фікстура
from tests.test_identity import auth, login, make_user
from tests.test_ingestion import TODAY, wb_payload
from tests.test_uploads import SOURCE, months_ago

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="БД не налаштовано")
UNEMP_DS = str(sid("open", "UNEMP"))


def rnd(n=2) -> str:
    return "".join(random.choices(string.ascii_uppercase, k=n))


def last_change(object_type: str, object_id: str) -> dict:
    with new_session() as db:
        e = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.object_type == object_type, AuditEvent.object_id == object_id)
            .order_by(AuditEvent.seq.desc())
        ).first()
        assert e is not None and e.action == AuditAction.CONFIG_CHANGE
        return json.loads(e.details)["changes"]


# ---------------------------------------------------------------------------- гість (S-36)
def test_guest_sees_only_about_and_registration():
    c = TestClient(app)
    for path in ("/api/catalog/datasets", "/api/coverage", "/api/indicators/UNEMP"):
        assert c.get(path).status_code == 401, path
    assert c.get("/api/countries").status_code == 200  # довідник потрібен формі реєстрації


# ---------------------------------------------------------------------------- API-ключі (FR-12, NFR-18)
def test_api_key_reads_data_and_is_limited(unemp, monkeypatch):
    c = TestClient(app)
    h = auth(login(c, make_user().email))
    r = c.post("/api/me/api-keys", headers=h)
    assert r.status_code == 201
    key = r.json()["key"]
    assert key.startswith(r.json()["prefix"] + "_")
    assert all("key" not in k for k in c.get("/api/me/api-keys", headers=h).json())  # повний ключ — лише раз

    k = {"X-API-Key": key}
    obs = c.get(f"/api/datasets/{UNEMP_DS}/observations", headers=k, params={"countries": "UKR"})
    assert obs.status_code == 200 and obs.json()["items"]
    f = c.get(f"/api/datasets/{UNEMP_DS}/export", headers=k, params={"format": "json"})
    assert f.status_code == 200
    with new_session() as db:
        exp = db.scalars(
            select(DataExport).where(DataExport.file_hash == f.headers["x-content-sha256"])
        ).one()
        assert exp.channel.value == "API"
    assert c.get("/api/countries/UKR/profile", headers=k).status_code == 200
    # ключ не відкриває профіль, заявки й адміністрування
    assert c.get("/api/me", headers=k).status_code == 401
    assert c.get("/api/access-requests", headers=k).status_code == 401
    assert c.get("/api/catalog/datasets", headers={"X-API-Key": key + "x"}).status_code == 401

    # NFR-18: 60 запитів за хвилину на ключ
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    ratelimit.reset()
    codes = [c.get("/api/countries/UKR/profile", headers=k).status_code for _ in range(61)]
    assert codes[:60] == [200] * 60 and codes[60] == 429
    ratelimit.reset()
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", False)

    # відкликання; не більше 3 чинних ключів
    kid = r.json()["id"]
    assert c.post(f"/api/me/api-keys/{kid}/revoke", headers=h).json()["revoked_at"]
    assert c.get("/api/catalog/datasets", headers=k).json()["detail"] == "API_KEY_INVALID"
    for _ in range(3):
        assert c.post("/api/me/api-keys", headers=h).status_code == 201
    assert c.post("/api/me/api-keys", headers=h).json()["detail"] == "API_KEY_LIMIT"


def test_provider_uploads_with_api_key():
    """FR-37: постачальник завантажує набір через REST API з ключем; аналітику ключ цього не дає."""
    c = TestClient(app)
    code = f"T{uuid.uuid4().hex[:8].upper()}"
    with new_session() as db:
        db.add(DataCategory(code=code, name_ua=f"Категорія {code}", name_en=f"Category {code}"))
        db.commit()
    provider = make_user(Role.DATA_PROVIDER, org_id=sid("org", "stat_ukr"))
    key = c.post("/api/me/api-keys", headers=auth(login(c, provider.email))).json()["key"]
    rows = "\n".join(f"UKR,R_CPI,Київ,{months_ago(n)},{100 + n / 10},{code}" for n in (3, 2, 1))
    form = {"category_code": code, "title_ua": f"Ціни {code}", "title_en": f"Prices {code}", **SOURCE}
    files = {"file": ("p.csv", f"country,indicator,breakdown,period,value,note\n{rows}\n".encode())}
    r = c.post("/api/provider/uploads", headers={"X-API-Key": key}, data=form, files=files)
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["status"] == "PENDING_REVIEW"
    assert c.get(f"/api/dataset-versions/{v['id']}", headers={"X-API-Key": key}).status_code == 200

    analyst_key = c.post("/api/me/api-keys", headers=auth(login(c, make_user().email))).json()["key"]
    assert (
        c.post(
            "/api/provider/uploads", headers={"X-API-Key": analyst_key}, data=form, files=files
        ).status_code
        == 403
    )


# ---------------------------------------------------------------------------- конектори (FR-17, UC-20)
def test_connectors_schedule_enable_and_audit(monkeypatch):
    c = TestClient(app)
    admin = auth(login(c, make_user(Role.ADMIN).email))
    assert c.get("/api/admin/connectors", headers=auth(login(c, make_user().email))).status_code == 403
    items = c.get("/api/admin/connectors", headers=admin).json()
    assert len(items) == 10 and all("runs" in x and "next_run" in x for x in items)
    unemp_c = next(x for x in items if x["indicator"] == "UNEMP")
    cid, old = unemp_c["id"], unemp_c["schedule"]

    bad = c.patch(f"/api/admin/connectors/{cid}", headers=admin, json={"schedule": "0 25 * * *"})
    assert bad.status_code == 422 and bad.json()["detail"] == "INVALID_SCHEDULE"  # EX1
    r = c.patch(f"/api/admin/connectors/{cid}", headers=admin, json={"schedule": "0  3 1 * *"})
    assert r.json()["schedule"] == "0 3 1 * *" and r.json()["next_run"].endswith("-01")
    assert last_change("connector", "WB UNEMP")["schedule"] == [old, "0 3 1 * *"]  # старе й нове значення
    assert (
        c.patch(f"/api/admin/connectors/{cid}", headers=admin, json={"is_enabled": False}).json()[
            "is_enabled"
        ]
        is False
    )
    c.patch(f"/api/admin/connectors/{cid}", headers=admin, json={"schedule": old, "is_enabled": True})

    calls = []
    monkeypatch.setattr("app.api.admin_config._manual_run", lambda cid_, rid: calls.append(cid_))
    assert c.post(f"/api/admin/connectors/{cid}/run", headers=admin).status_code == 202  # AC1
    assert str(calls[0]) == cid


def test_connector_csv_fallback():
    """UC-20 AC4: офіційний CSV-експорт World Bank → ті самі кроки, що й імпорт з API."""
    c = TestClient(app)
    admin = auth(login(c, make_user(Role.ADMIN).email))
    cid = str(sid("connector", "UNEMP"))
    u = round(4 + random.random(), 3)
    wb_csv = (
        '"Data Source","World Development Indicators",\n\n"Last Updated Date","2026-11-20",\n\n'
        '"Country Name","Country Code","Indicator Name","Indicator Code","2023","2024",\n'
        f'"Ukraine","UKR","Unemployment, total","SL.UEM.TOTL.ZS","{u}","{u + 0.5}",\n'
        '"Poland","POL","Unemployment, total","SL.UEM.TOTL.ZS","2.8","2.9",\n'
        '"Mars","MRS","Unemployment, total","SL.UEM.TOTL.ZS","1","1",\n'
    ).encode()
    r = c.post(
        f"/api/admin/connectors/{cid}/upload",
        headers=admin,
        files={"file": ("API_SL.UEM.TOTL.ZS.csv", wb_csv)},
    )
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "SUCCEEDED" and run["records"] == 4 and run["request_url"].startswith("file:")
    assert run["version_id"] and len(run["raw_sha256"]) == 64
    long = f"country,year,value\nUKR,2024,{u + 0.7}\n".encode()
    assert (
        c.post(
            f"/api/admin/connectors/{cid}/upload", headers=admin, files={"file": ("x.csv", long)}
        ).status_code
        == 200
    )
    wrong = (
        b'"Country Name","Country Code","Indicator Name","Indicator Code","2024"\n'
        b'"Ukraine","UKR","GDP","NY.GDP","1"\n'
    )
    assert (
        c.post(f"/api/admin/connectors/{cid}/upload", headers=admin, files={"file": ("y.csv", wrong)}).json()[
            "detail"
        ]
        == "UNSUPPORTED_CSV"
    )


def test_scheduled_run_only_when_due():
    """S-37: щоденний запуск за розкладом бере лише ті конектори, чий cron припадає на сьогодні."""
    payload = wb_payload({("UKR", 2024): round(6 + random.random(), 3)})
    monday, tuesday = date(2026, 11, 23), date(2026, 11, 24)
    with new_session() as db:
        assert db.get(Connector, sid("connector", "UNEMP")).schedule == "0 3 * * 1"
        kw = {"get": lambda url: payload}
        assert service.run_all(db, RunTrigger.SCHEDULE, "t", only={"UNEMP"}, today=tuesday, **kw) == []
        ran = service.run_all(db, RunTrigger.SCHEDULE, "t", only={"UNEMP"}, today=monday, **kw)
        assert [x["connector"] for x in ran] == ["WB UNEMP"]
        assert service.run_all(
            db, RunTrigger.MANUAL, "t", only={"UNEMP"}, today=tuesday, **kw
        )  # вручну — завжди
    assert TODAY  # імпортовано для єдиного «сьогодні» з test_ingestion


# ------------------------------------------------------------------- довідники (FR-43, UC-20 AC2, AC3, EX2)
def test_dictionaries():
    c = TestClient(app)
    admin = auth(login(c, make_user(Role.ADMIN).email))
    analyst = auth(login(c, make_user().email))
    code = "Q" + rnd()
    body = {"code": code, "name_ua": "Тестленд", "name_en": "Testland", "iso2": rnd(), "iso_numeric": "999"}
    assert c.post("/api/admin/countries", headers=analyst, json=body).status_code == 403
    assert c.post("/api/admin/countries", headers=admin, json=body).status_code == 201
    assert c.post("/api/admin/countries", headers=admin, json=body).json()["detail"] == "ALREADY_EXISTS"
    assert c.post("/api/admin/countries", headers=admin, json={**body, "code": "qq"}).status_code == 422
    country = next(x for x in c.get("/api/countries").json() if x["code"] == code)
    assert country["iso_numeric"] == "999" and country["is_active"]
    r = c.patch(f"/api/admin/countries/{code}", headers=admin, json={"is_active": False})
    assert r.json()["is_active"] is False and last_change("country", code)["is_active"] == [True, False]

    ind = "TEST_" + rnd(4)
    new = {"code": ind, "name_ua": "Тестовий", "name_en": "Test", "definition": "Показник для тесту",
           "unit": "%", "scale": "частка", "frequency": "ANNUAL", "hard_min": 0, "hard_max": 100}  # fmt: skip
    assert c.post("/api/admin/indicators", headers=admin, json=new).status_code == 201
    bad = c.patch(f"/api/admin/indicators/{ind}", headers=admin, json={"typical_min": 50, "typical_max": 200})
    assert bad.json()["detail"] == "INVALID_RANGES"
    r = c.patch(f"/api/admin/indicators/{ind}", headers=admin, json={"name_ua": "Тестовий 2"})
    assert r.json()["name_ua"] == "Тестовий 2" and last_change("indicator", ind)["name_ua"] == [
        "Тестовий",
        "Тестовий 2",
    ]
    assert c.delete(f"/api/admin/indicators/{ind}", headers=admin).status_code == 204
    assert (
        c.delete("/api/admin/indicators/UNEMP", headers=admin).json()["detail"] == "INDICATOR_HAS_DATA"
    )  # EX2
    assert (
        c.patch("/api/admin/indicators/UNEMP", headers=admin, json={"frequency": "MONTHLY"}).status_code
        == 409
    )

    cat = "C" + rnd(5)
    assert (
        c.post(
            "/api/admin/categories", headers=admin, json={"code": cat, "name_ua": "Тест", "name_en": "Test"}
        ).status_code
        == 201
    )
    r = c.patch(f"/api/admin/categories/{cat}", headers=admin, json={"name_en": "Test 2"})
    assert r.json()["name_en"] == "Test 2" and last_change("category", cat)["name_en"] == ["Test", "Test 2"]
    assert (
        c.patch(f"/api/admin/categories/{cat}", headers=admin, json={"name_en": "Test 2"}).json()["detail"]
        == "NOTHING_TO_UPDATE"
    )
