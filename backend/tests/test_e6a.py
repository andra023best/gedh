# ruff: noqa: F811 — фікстура world імпортується з test_exchange
"""Етап Е6а: функції Should для керування даними (S-33).

FR-38 (призупинення дозволу), UC-19 AC2 (нагадування), FR-29 (сповіщення в інтерфейсі),
FR-33 (походження набору), FR-41 (експорт власних ПД), FR-35 (відновлення пароля).
"""

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.api.jobs import expire_grants
from app.core import mail
from app.core.db import new_session
from app.models.audit import AuditEvent
from app.models.enums import AuditAction, Role
from app.models.exchange import AccessGrant
from app.models.technical import ActionToken, Notification
from tests.test_exchange import new_request, publish_dataset, world  # noqa: F401 — фікстура
from tests.test_identity import PASSWORD, auth, last_token, login, make_user

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="БД не налаштовано")


def grant_for(w) -> tuple[str, dict]:
    """Набір опубліковано → заявка → два погодження → угода → дозвіл. Повертає (dataset_id, grant)."""
    c = w["c"]
    dataset_id = publish_dataset(w)
    rid = new_request(w).json()["id"]
    assert c.post(
        f"/api/access-requests/{rid}/admin-decision", headers=w["admin"], json={"decision": "APPROVED"}
    )
    r = c.post(
        f"/api/access-requests/{rid}/provider-decision", headers=w["provider"], json={"decision": "APPROVED"}
    )
    assert r.json()["status"] == "AWAITING_AGREEMENT", r.text
    dsa = c.get(f"/api/access-requests/{rid}/agreement", headers=w["rep"]).json()
    g = c.post(
        f"/api/access-requests/{rid}/agreement/accept", headers=w["rep"], json={"text_hash": dsa["text_hash"]}
    )
    assert g.status_code == 201, g.text
    return dataset_id, g.json()


def notifications(headers, c) -> dict:
    return c.get("/api/notifications", headers=headers).json()


def test_dg_officer_suspends_grant_immediately(world):
    """FR-38, UC-19 AC1 і EX1: лише DG-офіцер, лише з причиною; доступ закривається одразу."""
    w, c = world, world["c"]
    dataset_id, grant = grant_for(w)
    gid = grant["id"]
    assert c.get(f"/api/datasets/{dataset_id}/observations", headers=w["rep"]).status_code == 200

    reason = {"reason": "Ознаки передачі даних третім особам"}
    assert c.post(f"/api/grants/{gid}/suspend", headers=w["admin"], json=reason).status_code == 403  # DG-07
    assert (
        c.post(f"/api/grants/{gid}/suspend", headers=w["dg"], json={"reason": "так"}).status_code == 422
    )  # EX1
    r = c.post(f"/api/grants/{gid}/suspend", headers=w["dg"], json=reason)
    assert r.status_code == 200 and r.json()["status"] == "SUSPENDED"
    assert r.json()["suspension_reason"] == reason["reason"]
    assert c.post(f"/api/grants/{gid}/suspend", headers=w["dg"], json=reason).status_code == 409

    denied = c.get(f"/api/datasets/{dataset_id}/observations", headers=w["rep"])
    assert denied.status_code == 403 and denied.json()["detail"] == "GRANT_SUSPENDED"
    with new_session() as db:
        e = db.scalars(
            select(AuditEvent).where(
                AuditEvent.action == AuditAction.GRANT_SUSPEND, AuditEvent.object_id == gid
            )
        ).one()
        assert json.loads(e.details)["reason"] == reason["reason"] and e.actor_role == Role.DG_OFFICER

    # учасників повідомлено (UC-19 AC1 крок 3) — в інтерфейсі
    titles = [n["title"] for n in notifications(w["rep"], c)["items"]]
    assert "дозвіл призупинено" in titles
    assert any(n["title"] == "дозвіл на дані призупинено" for n in notifications(w["provider"], c)["items"])

    # DG-12: після призупинення — підтвердити видалення, інакше нові заявки організації не приймаються
    assert new_request(w).json()["detail"] == "DELETION_CONFIRMATION_REQUIRED"
    assert c.post(f"/api/grants/{gid}/confirm-deletion", headers=w["rep"]).status_code == 200
    assert new_request(w).json().get("detail") != "DELETION_CONFIRMATION_REQUIRED"


def test_notifications_in_interface(world):
    """FR-29: сповіщення про зміну статусу заявки з посиланням; прочитано / усі прочитано; лише свої."""
    w, c = world, world["c"]
    rid = new_request(w).json()["id"]
    feed = notifications(w["admin"], c)
    n = next(x for x in feed["items"] if x["link"] == f"/requests?id={rid}")
    assert n["title"] == "нова заявка на закриті дані" and n["read_at"] is None and feed["unread"] >= 1
    assert c.post(f"/api/notifications/{n['id']}/read", headers=w["rep"]).status_code == 404  # чуже
    assert c.post(f"/api/notifications/{n['id']}/read", headers=w["admin"]).json()["read_at"]

    c.post(
        f"/api/access-requests/{rid}/admin-decision",
        headers=w["admin"],
        json={"decision": "REJECTED", "reason": "Мета не відповідає"},
    )
    mine = notifications(w["rep"], c)
    assert (
        mine["items"][0]["link"] == f"/requests?id={rid}" and "Мета не відповідає" in mine["items"][0]["text"]
    )
    assert c.post("/api/notifications/read-all", headers=w["rep"]).json()["marked"] >= 1
    assert notifications(w["rep"], c)["unread"] == 0
    assert c.get("/api/notifications").status_code == 401


def test_expiry_reminder_sent_once(world):
    """UC-19 AC2: за 7 днів до завершення — одне нагадування, навіть якщо задача запускається щодня."""
    w = world
    _, grant = grant_for(w)
    with new_session() as db:
        db.get(AccessGrant, grant["id"]).valid_to = datetime.now(UTC) + timedelta(days=3)
        db.commit()
    assert expire_grants("test-remind")["reminded"] >= 1
    expire_grants("test-remind-2")
    with new_session() as db:
        sent = db.scalar(
            select(func.count()).where(
                Notification.kind == "GRANT_EXPIRY_REMINDER", Notification.ref == grant["id"]
            )
        )
    assert sent == 1


def test_lineage_scope_by_role(world):
    """FR-33, DG-16: джерело → DQ → версія → дозволи → вивантаження; чужі дозволи — лише DG і власник."""
    w, c = world, world["c"]
    dataset_id, grant = grant_for(w)
    f = c.get(f"/api/datasets/{dataset_id}/export?format=csv", headers=w["rep"])
    sha = f.headers["x-content-sha256"]

    full = c.get(f"/api/datasets/{dataset_id}/lineage", headers=w["dg"]).json()
    assert full["scope"] == "all"
    v = full["versions"][0]
    assert v["source"]["document_title"] and v["source"]["responsible_person"]  # першоджерело
    assert len(v["file_sha256"]) == 64 and v["quality"]["score"] == 100  # хеш файлу, DQ-звіт
    assert v["published_by_role"] == "DG_OFFICER" and v["uploaded_by_role"] == "DATA_PROVIDER"
    assert any(x["file_sha256"] == sha for x in v["exports"])  # вивантаження з хешем
    assert [g["id"] for g in full["grants"]] == [grant["id"]]
    assert c.get(f"/api/datasets/{dataset_id}/lineage", headers=w["provider"]).json()["scope"] == "all"

    own = c.get(f"/api/datasets/{dataset_id}/lineage", headers=w["rep"]).json()
    assert own["scope"] == "own" and len(own["grants"]) == 1 and own["versions"][0]["exports"]

    stranger = auth(login(c, make_user().email))
    r = c.get(f"/api/datasets/{dataset_id}/lineage", headers=stranger)
    assert r.status_code == 403 and r.json()["detail"] == "ACCESS_DENIED"


def test_personal_data_export(world):
    """FR-41, UC-21 AC1: JSON з профілем, заявками, історією входів; хеш у заголовку й журналі."""
    w, c = world, world["c"]
    number = new_request(w).json()["number"]
    r = c.get("/api/me/export", headers=w["rep"])
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert r.headers["x-content-sha256"] == hashlib.sha256(r.content).hexdigest()
    data = r.json()
    assert data["profile"]["email"].endswith("@example.org")
    assert len(data["login_history"]) >= 1 and data["consents"][0]["action"] == "GIVEN"
    assert [x["number"] for x in data["access_requests"]] == [number]
    with new_session() as db:
        e = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.action == AuditAction.PERSONAL_DATA_EXPORT)
            .order_by(AuditEvent.seq.desc())
            .limit(1)
        ).one()
        assert json.loads(e.details)["file_sha256"] == r.headers["x-content-sha256"]


def test_password_reset():
    """FR-35: одноразове посилання на 1 год; однакова відповідь для невідомого e-mail; сесії відкликано."""
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    u = make_user()
    old = login(c, u.email)
    unknown = c.post("/api/auth/password-reset/request", json={"email": "nobody@example.invalid"})
    known = c.post("/api/auth/password-reset/request", json={"email": u.email.upper()})
    assert unknown.status_code == known.status_code == 202 and unknown.json() == known.json()
    assert mail.OUTBOX[-1].to == u.email
    token = last_token("/reset-password")

    weak = c.post("/api/auth/password-reset/confirm", json={"token": token, "password": "short"})
    assert weak.status_code == 422
    new_password = "Нове-сильне-гасло-2026"
    ok = c.post("/api/auth/password-reset/confirm", json={"token": token, "password": new_password})
    assert ok.status_code == 200, ok.text
    again = c.post("/api/auth/password-reset/confirm", json={"token": token, "password": new_password})
    assert again.json()["detail"] == "TOKEN_INVALID"  # одноразове

    assert c.post("/api/auth/login", json={"email": u.email, "password": PASSWORD}).status_code == 401
    login(c, u.email, new_password)
    # стара сесія відкликана: оновлення токена за старим cookie неможливе
    c2 = TestClient(app)
    c2.cookies.set("gedh_refresh", old.get("refresh_token", "x"), path="/api/auth")
    assert c2.post("/api/auth/refresh").status_code in (401, 403)

    # прострочене посилання
    c.post("/api/auth/password-reset/request", json={"email": u.email})
    token2 = last_token("/reset-password")
    with new_session() as db:
        row = db.scalars(
            select(ActionToken)
            .where(ActionToken.kind == "RESET_PASSWORD")
            .order_by(ActionToken.created_at.desc())
        ).first()
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
    expired = c.post(
        "/api/auth/password-reset/confirm", json={"token": token2, "password": new_password + "x"}
    )
    assert expired.status_code == 410


def test_review_reminder_after_five_business_days(world):
    """UC-09 EX2 (S-39): заявку не розглянуто 5 робочих днів — одне нагадування адміністратору."""
    from app.models.exchange import AccessRequest

    w = world
    req = new_request(w).json()
    with new_session() as db:
        db.get(AccessRequest, req["id"]).submitted_at = datetime.now(UTC) - timedelta(days=9)
        db.commit()
    assert expire_grants("test-review")["review_reminders"] >= 1
    expire_grants("test-review-2")
    with new_session() as db:
        sent = db.scalars(
            select(Notification).where(Notification.kind == "REVIEW_REMINDER", Notification.ref == req["id"])
        ).all()
    assert sent and len({n.user_id for n in sent}) == len(sent)  # кожному адміністратору — один раз
    assert any(n.link == f"/requests?id={req['id']}" for n in sent)


def test_import_problems_are_reported_to_admins():
    """UC-14 EX1, DG-22 (S-39): невдалий імпорт — зведення адміністраторам; наявні дані не змінюються."""
    from app.core.db import new_session as ns
    from app.ingestion import service
    from app.models.enums import RunTrigger

    admin = make_user(Role.ADMIN)

    def down(url):
        raise OSError("source is down")

    with ns() as db:
        out = service.run_all(
            db, RunTrigger.MANUAL, "test-import-fail", only={"UNEMP"}, get=down, sleep=lambda s: None
        )
    assert out[0]["status"] == "FAILED"
    with ns() as db:
        n = db.scalars(
            select(Notification).where(
                Notification.user_id == admin.id, Notification.kind == "IMPORT_SUMMARY"
            )
        ).one()
        assert "WB UNEMP" in n.text and "джерело недоступне" in n.text and n.link == "/admin/connectors"
