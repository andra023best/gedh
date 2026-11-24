"""Етап Е6б: аналітика відкритих даних (S-35).

UC-04 / FR-06, FR-07 (карта покриття), UC-05 / FR-08 (профіль країни), FR-09 (картка показника),
UC-06 / FR-10, FR-36 (порівняння й рейтинг — на основі рядів observations).
Відкриті дані тест імпортує сам (фальшива відповідь World Bank), тож не залежить від порядку тестів (S-31).
"""

import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import new_session
from app.ingestion import service
from app.main import app
from app.models.audit import AuditEvent
from app.models.enums import AuditAction, Role, RunStatus, RunTrigger
from app.seed.__main__ import sid
from tests.test_identity import auth, login, make_user
from tests.test_ingestion import TODAY, connector, wb_payload

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="БД не налаштовано")


@pytest.fixture
def unemp():
    """Нова версія UNEMP з унікальними значеннями для UKR (2023, 2024) і POL (2024)."""
    u1 = round(5 + uuid.uuid4().int % 1000 / 1000, 3)
    u2 = round(u1 + 1.25, 3)
    payload = wb_payload({("UKR", 2023): u1, ("UKR", 2024): u2, ("POL", 2024): 2.9})
    with new_session() as db:
        run = service.run_connector(
            db, connector(db), RunTrigger.MANUAL, "test-e6b", get=lambda u: payload, today=TODAY
        )
        assert run.status == RunStatus.SUCCEEDED and run.produced_version_id
    return u1, u2


def test_coverage_lists_indicators_for_guest(unemp):
    """UC-04 кроки 2–3, 5 (FR-06, FR-07): гість бачить, які показники є в країні, без значень."""
    cov = TestClient(app).get("/api/coverage").json()
    ukr = next(c for c in cov if c["country"] == "UKR")
    assert "UNEMP" in ukr["codes"] and ukr["indicators"] == len(ukr["codes"])
    assert "value" not in json.dumps(cov)


def test_country_profile(unemp):
    """UC-05 (FR-08): останнє значення, зміна за рік, ряд, джерело, версія; закриті набори без значень."""
    u1, u2 = unemp
    c = TestClient(app)
    assert c.get("/api/countries/UKR/profile").status_code == 401
    analyst = make_user(Role.ANALYST)
    h = auth(login(c, analyst.email))
    assert c.get("/api/countries/XXX/profile", headers=h).status_code == 404

    p = c.get("/api/countries/ukr/profile", headers=h).json()
    assert p["country"]["code"] == "UKR" and p["country"]["name_ua"] == "Україна"
    i = next(x for x in p["indicators"] if x["code"] == "UNEMP")
    assert [s["period"] for s in i["series"]] == ["2023", "2024"]
    assert i["latest"]["period"] == "2024" and i["latest"]["value"] == pytest.approx(u2)
    assert i["previous"]["value"] == pytest.approx(u1) and i["change"] == pytest.approx(1.25)
    assert i["source"].startswith("World Bank") and i["version_no"] >= 1 and i["published_at"]
    # закриті набори — лише опис і позначка, значень немає
    prices = next(r for r in p["restricted"] if r["id"] == str(sid("dataset", "ukr_prices")))
    assert prices["is_synthetic"] and "items" not in prices

    with new_session() as db:
        e = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.actor_id == analyst.id, AuditEvent.object_type == "country")
            .order_by(AuditEvent.seq.desc())
        ).first()
        assert e.action == AuditAction.DATA_VIEW and e.object_id == "UKR"
        assert json.loads(e.details)["view"] == "country_profile"
    # DG-офіцер знаходить перегляд профілю фільтром країни в журналі (UC-15)
    dg = auth(login(c, make_user(Role.DG_OFFICER).email))
    items = c.get(
        "/api/audit/events", headers=dg, params={"country": "UKR", "actor_id": str(analyst.id)}
    ).json()["items"]
    assert any(x["object_type"] == "country" for x in items)


def test_indicator_card_is_public_and_has_no_values(unemp):
    """FR-09 (UC-04 AC1): метадані, джерело, класифікація, покриття країн — без значень."""
    c = TestClient(app)
    card = c.get("/api/indicators/unemp").json()
    assert card["code"] == "UNEMP" and card["classification"] == "OPEN" and card["source"] == "WB"
    assert card["definition"] and card["unit"] and card["frequency"] == "ANNUAL" and card["licence"]
    ukr = next(x for x in card["coverage"] if x["country"] == "UKR")
    assert ukr["to"] == "2024"
    assert "value" not in json.dumps(card["coverage"])
    restricted = c.get("/api/indicators/R_CPI").json()
    assert restricted["classification"] == "RESTRICTED" and restricted["source"] is None
    assert c.get("/api/indicators/NOPE").status_code == 404


def test_comparison_series_only_for_selected_countries(unemp):
    """UC-06 (FR-10, FR-36): ряди лише обраних країн; без фільтра — усі країни для рейтингу."""
    c = TestClient(app)
    h = auth(login(c, make_user().email))
    ds = str(sid("open", "UNEMP"))
    picked = c.get(f"/api/datasets/{ds}/observations", headers=h, params={"countries": "UKR,POL"}).json()
    assert {o["country"] for o in picked["items"]} == {"UKR", "POL"}
    everyone = c.get(f"/api/datasets/{ds}/observations", headers=h).json()
    assert {o["country"] for o in everyone["items"]} >= {"UKR", "POL"}
