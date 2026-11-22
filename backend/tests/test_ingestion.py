"""Етап Е2+: імпорт відкритих даних (UC-14, UC-18; FR-14 – FR-16; NFR-11; DG-22).

Джерела підмінено фікстурами (формати відповідей — як у живих API 22.11.2026), тож тести не ходять у мережу.
"""

import json
import os
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.db import new_session
from app.ingestion import quality, service, sources
from app.main import app
from app.models.datasets import DatasetVersion, Observation
from app.models.enums import Frequency, RunStatus, RunTrigger, Severity, VersionStatus
from app.models.ingestion import Connector, ImportRun
from app.models.technical import RawPayload
from app.seed.__main__ import sid

TODAY = date(2026, 11, 22)


# ---------------------------------------------------------------------------- гармонізація (без БД)
def wb_payload(values: dict[tuple[str, int], float | None]) -> bytes:
    items = [{"countryiso3code": c, "date": str(y), "value": v} for (c, y), v in values.items()]
    return json.dumps([{"page": 1}, items]).encode()


def test_wb_scales_and_skips_nulls():
    raw = wb_payload({("UKR", 2024): 190_833_800_000.0, ("UKR", 2023): None, ("XXX", 2024): 1.0})
    res = sources.fetch(sources.SPECS["GDP_USD"], ["UKR", "POL"], 2025, get=lambda url: raw)
    assert [(r.country, r.period_start, r.value) for r in res.rows] == [("UKR", date(2024, 1, 1), 190.8338)]
    assert "NY.GDP.MKTP.CD" in res.request_url and res.raw == raw


def test_imf_drops_projections_and_marks_estimate():
    raw = json.dumps(
        {
            "values": {
                "GGXWDG_NGDP": {"UKR": {"2024": 89.7, "2025": 107.1, "2026": 111.8}, "SDN": {"2024": 1.0}}
            }
        }
    ).encode()
    res = sources.fetch(sources.SPECS["GOV_DEBT"], ["UKR"], 2025, get=lambda url: raw)
    assert [(r.period_start.year, r.value, r.note) for r in res.rows] == [
        (2024, 89.7, None),
        (2025, 107.1, "оцінка IMF WEO"),
    ]  # прогноз 2026 і чужі країни не імпортуються


def test_bis_takes_december_and_euro_area_rate():
    raw = (
        b"FREQ,REF_AREA,TIME_PERIOD,OBS_VALUE\nM,XM,2024-11,3.25\nM,XM,2024-12,3\n"
        b"M,US,2024-12,4.375\nM,US,2025-06,4.5\n"
    )
    urls = []
    res = sources.fetch(
        sources.SPECS["POLICY_RATE"], ["DEU", "USA", "UKR"], 2025, get=lambda url: urls.append(url) or raw
    )
    got = {(r.country, r.period_start.year): (r.value, r.note) for r in res.rows}
    assert got == {("DEU", 2024): (3.0, "ставка ЄЦБ (ряд BIS XM)"), ("USA", 2024): (4.375, None)}
    assert "M.UA+US+XM" in urls[0]  # Україна запитується, але BIS її не має (D-37)


# ---------------------------------------------------------------------------- правила якості (без БД)
RULES = {"UNEMP": quality.IndicatorRules("UNEMP", Frequency.ANNUAL, 0, 100, 0.5, 35)}


def row(year, value, country="UKR", indicator="UNEMP"):
    return quality.Row(country, indicator, date(year, 1, 1), date(year, 12, 31), value)


def test_quality_rules():
    rows = [
        row(2023, 9.0),
        row(2024, 150.0),
        row(2024, 9.0),
        row(2025, 40.0),
        row(2025, 5.0, country="ZZZ"),
        quality.Row("UKR", "UNEMP", date(2025, 1, 1), date(2025, 6, 30), 8.0),
    ]
    rep = quality.evaluate(
        rows,
        RULES,
        {"UKR"},
        previous={("UKR", "UNEMP", None, date(2023, 1, 1), date(2023, 12, 31)): 5.0},
        today=TODAY,
    )
    rules = {(i.rule, i.severity) for i in rep.issues}
    assert ("Q-06", Severity.CRITICAL) in rules  # 150 % безробіття неможливо
    assert ("Q-05", Severity.CRITICAL) in rules  # повтор 2024
    assert ("Q-07", Severity.WARNING) in rules  # 40 % — нетипово
    assert ("Q-03", Severity.CRITICAL) in rules  # країна ZZZ
    assert ("Q-04", Severity.CRITICAL) in rules  # півроку для річного показника
    assert ("Q-08", Severity.WARNING) in rules  # перегляд 5 → 9 (+80 %)
    assert rep.has_critical and 0 <= rep.score < 100


def test_quality_timeliness_and_gaps():
    old_series = [row(y, 8.0) for y in (2010, 2011, 2021)]
    rep = quality.evaluate(old_series, RULES, {"UKR"}, today=TODAY)
    assert {i.rule for i in rep.issues} == {"Q-09", "Q-10"}  # пропуски 2012–2020 і дані лише до 2021
    fresh = quality.evaluate([row(2024, 8.0), row(2025, 8.1)], RULES, {"UKR"}, today=TODAY)
    assert fresh.issues == [] and fresh.score == 100.0


# ---------------------------------------------------------------------------- запуск конектора (з БД)
pytestmark_db = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="БД не налаштовано")


def connector(db, code="UNEMP") -> Connector:
    return db.get(Connector, sid("connector", code))


def versions(db, code="UNEMP"):
    return db.scalars(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == sid("open", code))
        .order_by(DatasetVersion.version_no)
    ).all()


@pytestmark_db
def test_run_creates_version_only_when_data_changes():
    with new_session() as db:
        before = len(versions(db))
        # унікальне значення: тест не залежить від того, які дані вже лежать у БД
        unique = round(5 + int(uuid.uuid4().int % 1000) / 1000, 3)
        payload = wb_payload({("UKR", 2024): unique, ("POL", 2024): 2.9, ("POL", 2025): 3.0})
        run = service.run_connector(
            db, connector(db), RunTrigger.MANUAL, "test", get=lambda u: payload, today=TODAY
        )
        assert run.status == RunStatus.SUCCEEDED and run.produced_version_id is not None
        assert db.get(RawPayload, db.scalars(select(RawPayload.id).where(RawPayload.run_id == run.id)).one())
        vs = versions(db)
        assert len(vs) == before + 1 and vs[-1].status == VersionStatus.PUBLISHED
        if before:
            assert (
                vs[-2].status == VersionStatus.SUPERSEDED and vs[-1].previous_id == vs[-2].id
            )  # D-05, lineage
        # той самий вміст → нової версії немає (FR-16)
        again = service.run_connector(
            db, connector(db), RunTrigger.MANUAL, "test", get=lambda u: payload, today=TODAY
        )
        assert again.status == RunStatus.SUCCEEDED and again.produced_version_id is None
        assert len(versions(db)) == before + 1


@pytestmark_db
def test_source_down_keeps_data_and_fails_run():
    calls = []

    def down(url):
        calls.append(url)
        raise TimeoutError("api down")

    with new_session() as db:
        before = [v.id for v in versions(db)]
        run = service.run_connector(
            db, connector(db), RunTrigger.SCHEDULE, "test", get=down, sleep=lambda s: None, today=TODAY
        )
        assert run.status == RunStatus.FAILED and "TimeoutError" in run.error_message
        assert len(calls) == 4  # перша спроба + 3 повторні (UC-14 EX1)
        assert [v.id for v in versions(db)] == before  # наявні дані не змінено (NFR-11)


@pytestmark_db
def test_critical_quality_blocks_new_version():
    with new_session() as db:
        before = [v.id for v in versions(db)]
        bad = wb_payload({("UKR", 2024): 250.0})  # безробіття 250 % — Q-06
        run = service.run_connector(
            db, connector(db), RunTrigger.MANUAL, "test", get=lambda u: bad, today=TODAY
        )
        assert run.status == RunStatus.REJECTED_BY_DQ and run.produced_version_id is None  # UC-14 EX2
        assert [v.id for v in versions(db)] == before


@pytestmark_db
def test_open_data_view_requires_login_and_restricted_is_denied():
    client = TestClient(app)
    catalog = client.get("/api/catalog/datasets").json()
    assert {d["kind"] for d in catalog} == {"OPEN", "RESTRICTED"}
    assert all(d["is_synthetic"] for d in catalog if d["kind"] == "RESTRICTED")  # FR-27
    unemp = str(sid("open", "UNEMP"))
    assert client.get(f"/api/datasets/{unemp}/observations").status_code == 401  # гість — без значень (L0)

    from tests.test_identity import auth, login, make_user

    s = auth(login(client, make_user().email))
    r = client.get(f"/api/datasets/{unemp}/observations?countries=UKR,POL", headers=s)
    assert r.status_code == 200 and r.json()["source"]["document_title"].startswith("World Bank")
    assert {i["country"] for i in r.json()["items"]} <= {"UKR", "POL"}
    denied = client.get(f"/api/datasets/{sid('dataset', 'ukr_prices')}/observations", headers=s)
    assert denied.status_code == 403 and denied.json()["detail"] == "ACCESS_DENIED"  # UC-16 EX2


@pytestmark_db
def test_job_endpoint_requires_token(monkeypatch):
    from app.core.config import get_settings

    client = TestClient(app)
    assert client.post("/api/internal/jobs/import").json()["detail"] == "JOB_TOKEN_INVALID"
    monkeypatch.setattr(get_settings(), "job_token", "t0ken")
    monkeypatch.setattr("app.api.jobs._import_job", lambda rid, only: None)
    r = client.post("/api/internal/jobs/import?indicator=UNEMP", headers={"X-Job-Token": "t0ken"})
    assert r.status_code == 202


@pytestmark_db
def test_coverage_counts_indicators():
    with new_session() as db:
        has_data = db.scalar(
            select(func.count(Observation.id))
            .join(DatasetVersion)
            .where(DatasetVersion.status == VersionStatus.PUBLISHED)
        )
    cov = TestClient(app).get("/api/coverage").json()
    assert has_data and any(c["country"] == "UKR" for c in cov)
    assert ImportRun  # використано в інших тестах
