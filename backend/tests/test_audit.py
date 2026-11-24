"""Етап Е5: журнал аудиту (UC-15; FR-30 – FR-32, FR-40; DG-07, DG-17 – DG-19, DG-23, DG-24)."""

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.jobs import apply_retention
from app.core import audit
from app.core.config import get_settings
from app.core.db import check_database, new_session
from app.main import app
from app.models.audit import AuditEvent
from app.models.enums import AccountStatus, AuditAction, AuditResult, Role
from app.models.identity import User
from app.models.technical import StoredFile
from app.seed.__main__ import sid
from tests.test_identity import PASSWORD, auth, login, make_user

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="БД не налаштовано")


@pytest.fixture
def c():
    return TestClient(app)


@pytest.fixture
def dg(c):
    u = make_user(Role.DG_OFFICER)
    return u, auth(login(c, u.email))


def events(**where) -> list[AuditEvent]:
    with new_session() as db:
        q = select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(20)
        for k, v in where.items():
            q = q.where(getattr(AuditEvent, k) == v)
        return list(db.scalars(q))


def test_only_dg_officer_sees_the_log_and_attempts_are_recorded(c, dg):
    """UC-15 EX1, DG-07, D-38: адміністратор і інші ролі — 403 зі записом AUDIT_VIEW / DENIED."""
    for role in (Role.ADMIN, Role.ANALYST, Role.DATA_PROVIDER):
        u = make_user(role)
        h = auth(login(c, u.email))
        for path in ("/api/audit/events", "/api/audit/events/1", "/api/audit/integrity", "/api/audit/export"):
            r = c.get(path, headers=h)
            assert r.status_code == 403, (role, path, r.text)
            assert r.json()["detail"] == "FORBIDDEN_ROLE"
        denied = events(actor_id=u.id, action=AuditAction.AUDIT_VIEW)
        assert len(denied) == 4 and all(e.result == AuditResult.DENIED for e in denied)
    assert c.get("/api/audit/events").status_code == 401


def test_list_filters_pagination_and_view_is_logged(c, dg):
    """UC-15 кроки 1–3 (FR-31): фільтри, від нових до старих, before_seq; сам перегляд записано."""
    officer, h = dg
    analyst = make_user(Role.ANALYST)
    for _ in range(3):  # три входи аналітика → три події LOGIN
        login(c, analyst.email)
    c.post("/api/auth/login", json={"email": analyst.email, "password": "wrong-password-1"})

    r = c.get("/api/audit/events", headers=h, params={"actor_email": analyst.email.upper(), "limit": 2})
    assert r.status_code == 200, r.text
    page = r.json()
    assert [e["action"] for e in page["items"]] == ["LOGIN_FAILED", "LOGIN"]
    assert page["items"][0]["result"] == "DENIED" and page["items"][0]["actor_label"] == analyst.email
    assert page["items"][0]["seq"] > page["items"][1]["seq"]
    assert page["next_before_seq"] == page["items"][1]["seq"]
    rest = c.get(
        "/api/audit/events",
        headers=h,
        params={"actor_id": str(analyst.id), "before_seq": page["next_before_seq"]},
    ).json()
    assert [e["action"] for e in rest["items"]] == ["LOGIN", "LOGIN"] and rest["next_before_seq"] is None

    only_failed = c.get(
        "/api/audit/events", headers=h, params={"actor_id": str(analyst.id), "result": "DENIED"}
    ).json()
    assert len(only_failed["items"]) == 1
    nobody = c.get("/api/audit/events", headers=h, params={"actor_email": "nobody@example.invalid"}).json()
    # невдалий вхід із незнайомим e-mail: виконавця немає, e-mail у журнал не пишеться (D-40)
    c.post("/api/auth/login", json={"email": "ghost@example.invalid", "password": "wrong-password-1"})
    ghost = c.get("/api/audit/events", headers=h, params={"action": "LOGIN_FAILED", "limit": 1}).json()[
        "items"
    ][0]
    assert ghost["actor_label"] == "невідомий e-mail" and "ghost" not in ghost["details"]
    assert nobody == {"items": [], "next_before_seq": None}
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert c.get("/api/audit/events", headers=h, params={"from": future}).json()["items"] == []
    assert c.get("/api/audit/events", headers=h, params={"country": "ukr"}).status_code == 422

    # постумова UC-15: факт перегляду записано, фільтр — без e-mail (D-40)
    views = events(actor_id=officer.id, action=AuditAction.AUDIT_VIEW)
    assert len(views) == 6  # запит із помилкою валідації (422) не виконується й не пишеться
    first = json.loads(views[-1].details)
    assert first["filters"] == {"actor_id": str(analyst.id)} and first["returned"] == 2
    assert analyst.email not in views[-1].details


def test_filter_by_dataset_and_country(c, dg):
    """UC-15 крок 2: набір і країна — через об'єкт події (набір, версія, дозвіл, заявка, організація).

    Лише демо-набір із seed: на чистій БД (CI) відкриті набори ще не мають версій до тесту імпорту.
    """
    _, h = dg
    analyst = make_user(Role.ANALYST)
    org = sid("org", "stat_ukr")
    provider = make_user(Role.DATA_PROVIDER, org_id=org)
    prices = str(sid("dataset", "ukr_prices"))
    # аналітик — відмова (UC-16 EX2); постачальник-власник — успіх
    assert (
        c.get(f"/api/datasets/{prices}/observations", headers=auth(login(c, analyst.email))).status_code
        == 403
    )
    assert (
        c.get(f"/api/datasets/{prices}/observations", headers=auth(login(c, provider.email))).status_code
        == 200
    )

    by_ds = c.get("/api/audit/events", headers=h, params={"dataset_id": prices, "limit": 200}).json()["items"]
    mine = {(e["actor_id"], e["result"]) for e in by_ds if e["action"] == "DATA_VIEW"}
    assert {(str(analyst.id), "DENIED"), (str(provider.id), "SUCCESS")} <= mine
    assert all(e["object_type"] in ("dataset", "dataset_version", "grant", "import_run") for e in by_ds)
    other = str(sid("dataset", "pol_trade"))
    assert not any(e["object_id"] == other for e in by_ds)

    # закритий набір України: відмову видно за країною набору, але не за іншою країною
    ukr = c.get("/api/audit/events", headers=h, params={"country": "UKR", "result": "DENIED", "limit": 200})
    assert any(e["actor_id"] == str(analyst.id) and e["object_id"] == prices for e in ukr.json()["items"])
    pol = c.get("/api/audit/events", headers=h, params={"country": "POL", "limit": 200}).json()["items"]
    assert not any(e["object_id"] == prices for e in pol)

    # фільтр за організацією, від імені якої діяли
    items = c.get("/api/audit/events", headers=h, params={"organization_id": str(org)}).json()["items"]
    assert items and all(e["on_behalf_of_id"] == str(org) for e in items)
    assert items[0]["organization_label"]


def test_event_detail_integrity_and_export(c, dg):
    """UC-15 крок 4 і 5, AC1 (FR-32, FR-40)."""
    officer, h = dg
    seq = c.get("/api/audit/events", headers=h, params={"limit": 1}).json()["items"][0]["seq"]
    e = c.get(f"/api/audit/events/{seq}", headers=h).json()
    assert e["hash_valid"] is True and len(e["hash"]) == 64 and len(e["prev_hash"]) == 64
    assert c.get("/api/audit/events/999999999", headers=h).status_code == 404

    r = c.get("/api/audit/integrity", headers=h)
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["first_broken_seq"] is None
    assert r.json()["checked"] == r.json()["last_seq"]  # усі записи з першого, без пропусків

    r = c.get("/api/audit/export", headers=h, params={"action": "AUDIT_VIEW", "actor_id": str(officer.id)})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    sha = hashlib.sha256(r.content).hexdigest()
    assert r.headers["x-content-sha256"] == sha
    text_ = r.content.decode("utf-8-sig")
    assert "seq,occurred_at,actor_id" in text_ and officer.email not in text_
    assert text_.count("AUDIT_VIEW") >= 3  # список, запис, перевірка цілісності
    exp = events(actor_id=officer.id, action=AuditAction.AUDIT_EXPORT)[0]
    assert json.loads(exp.details)["file_sha256"] == sha


def test_integrity_check_finds_the_first_tampered_record():
    """DG-19 / FR-32: якщо хтось обійде захист (вимкне тригер від імені власника), перевірка знайде запис.

    Усе — в одній транзакції, яку наприкінці скасовано: журнал не змінюється.
    """
    engine = create_engine(get_settings().sqlalchemy_url)
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            target = conn.execute(text("SELECT max(seq) - 1 FROM audit_events")).scalar_one()
            conn.execute(text("ALTER TABLE audit_events DISABLE TRIGGER trg_audit_events_no_update_delete"))
            conn.execute(
                text("UPDATE audit_events SET details = '{\"forged\": true}' WHERE seq = :s"), {"s": target}
            )
            assert audit.verify_chain(Session(bind=conn)) == target
        finally:
            trans.rollback()
    with new_session() as db:
        assert audit.verify_chain(db) is None


def test_database_forbids_changing_the_log():
    """DG-19: роль застосунку не має прав UPDATE/DELETE; тригер зупиняє навіть власника БД."""
    assert check_database()["app_role"] == "gedh_app"
    for sql in (
        "UPDATE audit_events SET details = 'x' WHERE seq = 1",
        "DELETE FROM audit_events WHERE seq = 1",
        "DELETE FROM audit_trail",
    ):
        with new_session() as db, pytest.raises(DBAPIError, match="permission denied"):
            db.execute(text(sql))
    owner = create_engine(get_settings().sqlalchemy_url)
    for sql, msg in (
        ("UPDATE audit_events SET details = 'x' WHERE seq = 1", "DG-19"),
        ("TRUNCATE audit_events", "DG-19"),
        ("UPDATE audit_trail SET last_seq = 0", "може лише зростати"),
    ):
        with owner.connect() as conn, pytest.raises(DBAPIError, match=msg):
            conn.execute(text(sql))
    # додавання працює як і раніше
    with new_session() as db:
        e = audit.append(
            db, action=AuditAction.SECURITY_INCIDENT, object_type="test", object_id="x", request_id="t"
        )
        db.commit()
        assert e.seq > 0


def test_retention_anonymizes_after_30_days_and_purges_files(c):
    """DG-23, DG-24: щоденна задача; журнал не змінюється, ланцюжок цілий."""
    now = datetime.now(UTC)
    gone = make_user(Role.ANALYST, status=AccountStatus.DEACTIVATED)
    recent = make_user(Role.ANALYST, status=AccountStatus.DEACTIVATED)
    with new_session() as db:
        db.get(User, gone.id).deactivated_at = now - timedelta(days=31)
        db.get(User, recent.id).deactivated_at = now - timedelta(days=5)
        audit.append(
            db,
            action=AuditAction.LOGIN,
            object_type="user",
            object_id=gone.id,
            request_id="t",
            actor_id=gone.id,
        )
        f_old = StoredFile(
            purpose="DATASET_UPLOAD",
            content_type="text/csv",
            size=1,
            sha256=uuid.uuid4().hex * 2,
            data=b"x",
            delete_after=now - timedelta(days=1),
        )
        f_keep = StoredFile(
            purpose="DATASET_UPLOAD",
            content_type="text/csv",
            size=1,
            sha256=uuid.uuid4().hex * 2,
            data=b"x",
            delete_after=now + timedelta(days=1),
        )
        db.add_all([f_old, f_keep])
        db.commit()
        old_id, keep_id = f_old.id, f_keep.id

    counts = apply_retention("test-retention", now)
    assert counts["accounts_anonymized"] >= 1 and counts["files_deleted"] >= 1
    with new_session() as db:
        g = db.get(User, gone.id)
        assert g.status == AccountStatus.ANONYMIZED and g.email.endswith("@anonymized.invalid")
        assert g.full_name == "Анонімізований користувач" and g.position is None
        assert db.get(User, recent.id).status == AccountStatus.DEACTIVATED
        assert db.get(StoredFile, old_id) is None and db.get(StoredFile, keep_id) is not None
        # журнал не змінився: ідентифікатор лишився псевдонімом, ланцюжок цілий (DG-24, DG-19)
        assert db.scalar(select(AuditEvent).where(AuditEvent.actor_id == gone.id)) is not None
        assert audit.verify_chain(db) is None
    purge = events(action=AuditAction.RETENTION_PURGE, request_id="test-retention")
    assert {json.loads(e.details)["category"] for e in purge} >= {"accounts_anonymized", "files_deleted"}
    assert events(action=AuditAction.ACCOUNT_DELETE, object_id=str(gone.id))
    # увійти анонімізованим обліковим записом неможливо
    r = c.post("/api/auth/login", json={"email": gone.email, "password": PASSWORD})
    assert r.status_code == 401
    assert apply_retention("test-retention-2", now)["accounts_anonymized"] == 0  # ідемпотентно
