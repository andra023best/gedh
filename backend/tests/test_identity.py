"""Етап Е2а: реєстрація, вхід, профіль, розгляд заявок (UC-01 – UC-03, UC-21; FR-01 – FR-05, FR-34, FR-42).

Потрібна БД з міграціями (як у CI). Кожен тест створює власних користувачів з унікальними e-mail.
"""

import os
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import audit, consent, mail, ratelimit, security
from app.core.config import get_settings
from app.core.db import new_session
from app.main import app
from app.models.audit import AuditEvent
from app.models.enums import AccountStatus, AuditAction, AuditResult, ConsentAction, Role
from app.models.identity import ConsentRecord, User
from app.models.technical import ActionToken

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="БД не налаштовано")

PASSWORD = "Strong-pass-2026"
PDF = b"%PDF-1.4\n% test document\n"


@pytest.fixture
def client():
    mail.OUTBOX.clear()
    ratelimit.reset()
    return TestClient(app)


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}.{uuid.uuid4().hex[:10]}@example.org"


def make_user(role: Role = Role.ANALYST, status: AccountStatus = AccountStatus.ACTIVE, org_id=None) -> User:
    with new_session() as db:
        u = User(
            full_name=f"Тест {role.value}",
            email=unique_email(role.value.lower()),
            password_hash=security.hash_password(PASSWORD),
            country_code="UKR",
            role=role,
            status=status,
            organization_id=org_id,
        )
        db.add(u)
        db.flush()
        db.add(ConsentRecord(user_id=u.id, text_version=consent.CURRENT_VERSION, action=ConsentAction.GIVEN))
        db.commit()
        return u


def login(client: TestClient, email: str, password: str = PASSWORD) -> dict:
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def auth(token_response: dict) -> dict:
    return {"Authorization": f"Bearer {token_response['access_token']}"}


def last_token(kind_path: str) -> str:
    m = re.search(rf"{kind_path}\?token=([\w-]+)", mail.OUTBOX[-1].text)
    assert m, mail.OUTBOX[-1].text
    return m.group(1)


def form(**over) -> dict:
    data = {
        "full_name": "Олена Тестова",
        "email": unique_email(),
        "password": PASSWORD,
        "country_code": "UKR",
        "account_type": "INDIVIDUAL",
        "purpose": "Аналіз макроекономічних показників для дипломної роботи",
        "consent_accepted": "true",
        "consent_text_version": consent.CURRENT_VERSION,
    }
    data.update(over)
    return data


def audit_actions(user_id) -> list[tuple[str, str]]:
    with new_session() as db:
        rows = db.scalars(select(AuditEvent).where(AuditEvent.actor_id == user_id).order_by(AuditEvent.seq))
        return [(e.action.value, e.result.value) for e in rows]


# ---------------------------------------------------------------------------- UC-01 → UC-02 → UC-03
def test_full_registration_flow(client):
    data = form()
    r = client.post("/api/auth/register", data=data)
    assert r.status_code == 202
    token = last_token("/verify-email")
    with new_session() as db:
        user = db.scalars(select(User).where(User.email == data["email"])).one()
        assert user.status == AccountStatus.PENDING_EMAIL
        assert user.password_hash.startswith("$argon2id$")  # NFR-14
        rec = db.scalars(select(ConsentRecord).where(ConsentRecord.user_id == user.id)).one()
        assert rec.text_version == consent.CURRENT_VERSION  # DG-10: версія й час згоди

    admin = make_user(Role.ADMIN)
    a = auth(login(client, admin.email))
    # до підтвердження e-mail заявки в черзі немає (FR-02)
    queue = client.get("/api/admin/registrations", headers=a).json()["items"]
    assert all(i["applicant"]["email"] != data["email"] for i in queue)
    # вхід до схвалення неможливий (UC-03 EX2)
    r = client.post("/api/auth/login", json={"email": data["email"], "password": PASSWORD})
    assert r.status_code == 403 and r.json()["status"] == "PENDING_EMAIL"

    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 200
    queue = client.get("/api/admin/registrations", headers=a).json()["items"]
    rr = next(i for i in queue if i["applicant"]["email"] == data["email"])
    r = client.post(f"/api/admin/registrations/{rr['id']}/approve", json={"role": "ANALYST"}, headers=a)
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"

    session = login(client, data["email"])
    assert session["user"]["role"] == "ANALYST" and session["consent_update_required"] is False
    me = client.get("/api/me", headers=auth(session)).json()
    assert me["email"] == data["email"] and me["consent"]["accepted_version"] == consent.CURRENT_VERSION
    assert any("анонімізація" in r["retention"] for r in me["retention"])  # UC-21 крок 2

    assert ("REGISTRATION_DECISION", "SUCCESS") in audit_actions(admin.id)
    applicant = audit_actions(uuid.UUID(me["id"]))
    assert ("CONSENT_CHANGE", "SUCCESS") in applicant and ("LOGIN", "SUCCESS") in applicant


def test_registered_email_is_not_disclosed(client):
    data = form()
    assert client.post("/api/auth/register", data=data).status_code == 202
    r = client.post("/api/auth/register", data={**data, "email": data["email"].upper()})
    assert r.status_code == 202  # UC-01 EX1: та сама відповідь
    assert "повторної реєстрації" in mail.OUTBOX[-1].subject
    with new_session() as db:
        assert len(db.scalars(select(User).where(User.email.ilike(data["email"]))).all()) == 1


@pytest.mark.parametrize(
    ("over", "status", "code"),
    [
        ({"consent_accepted": "false"}, 400, "CONSENT_REQUIRED"),
        ({"consent_text_version": "0.9"}, 400, "CONSENT_VERSION_OUTDATED"),
        ({"password": "short1"}, 422, "WEAK_PASSWORD"),
        ({"password": "onlyletterslong"}, 422, "WEAK_PASSWORD"),
        ({"email": "not-an-email"}, 422, "INVALID_EMAIL"),
        ({"country_code": "Ukraine"}, 422, "INVALID_COUNTRY_CODE"),
        (
            {"account_type": "ORGANIZATION_MEMBER", "organization_name": "Нова агенція"},
            422,
            "VERIFICATION_DOCUMENT_REQUIRED",
        ),
    ],
)
def test_registration_validation(client, over, status, code):
    r = client.post("/api/auth/register", data=form(**over))
    assert r.status_code == status and r.json()["detail"] == code


def test_organization_member_with_document(client):
    data = form(account_type="ORGANIZATION_MEMBER", organization_name="Нова агенція")
    fake = client.post(
        "/api/auth/register",
        data=data,
        files={"verification_document": ("doc.pdf", b"<html>not a pdf</html>", "application/pdf")},
    )
    assert fake.status_code == 415  # NFR-19: тип за вмістом, а не за назвою
    big = client.post(
        "/api/auth/register",
        data=data,
        files={"verification_document": ("big.pdf", PDF + b"0" * (2 * 1024 * 1024), "application/pdf")},
    )
    assert big.status_code == 413
    ok = client.post(
        "/api/auth/register", data=data, files={"verification_document": ("doc.pdf", PDF, "application/pdf")}
    )
    assert ok.status_code == 202
    client.post("/api/auth/verify-email", json={"token": last_token("/verify-email")})

    a = auth(login(client, make_user(Role.ADMIN).email))
    rr = next(
        i
        for i in client.get("/api/admin/registrations", headers=a).json()["items"]
        if i["applicant"]["email"] == data["email"]
    )
    assert rr["has_document"] and rr["organization_name"] == "Нова агенція"
    doc = client.get(f"/api/admin/registrations/{rr['id']}/document", headers=a)
    assert doc.content == PDF and doc.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in doc.headers["content-disposition"]
    # представник організації — лише з верифікованою організацією (FR-04)
    r = client.post(
        f"/api/admin/registrations/{rr['id']}/approve", json={"role": "ORG_REPRESENTATIVE"}, headers=a
    )
    assert r.json()["detail"] == "ORGANIZATION_REQUIRED"
    org = client.post(
        "/api/admin/organizations",
        headers=a,
        json={"name": "Нова агенція (тест)", "type": "ANALYTICS_COMPANY", "country_code": "UKR"},
    ).json()
    r = client.post(
        f"/api/admin/registrations/{rr['id']}/approve",
        headers=a,
        json={"role": "ORG_REPRESENTATIVE", "organization_id": org["id"]},
    )
    assert r.status_code == 200
    me = client.get("/api/me", headers=auth(login(client, data["email"]))).json()
    assert me["role"] == "ORG_REPRESENTATIVE" and me["organization"]["id"] == org["id"]


def test_clarification_flow(client):
    data = form()
    client.post("/api/auth/register", data=data)
    client.post("/api/auth/verify-email", json={"token": last_token("/verify-email")})
    a = auth(login(client, make_user(Role.ADMIN).email))
    rr = next(
        i
        for i in client.get("/api/admin/registrations", headers=a).json()["items"]
        if i["applicant"]["email"] == data["email"]
    )
    r = client.post(f"/api/admin/registrations/{rr['id']}/clarify", json={"note": "Уточніть мету"}, headers=a)
    assert r.json()["status"] == "NEEDS_CLARIFICATION"
    token = last_token("/registration-clarification")
    assert client.get(f"/api/auth/registration-clarification/{token}").json()["note"] == "Уточніть мету"
    r = client.post(f"/api/auth/registration-clarification/{token}", data={"answer": "Магістерська робота"})
    assert r.status_code == 200
    again = client.post(f"/api/auth/registration-clarification/{token}", data={"answer": "ще раз"})
    assert again.status_code == 400  # одноразове посилання
    assert client.get(f"/api/admin/registrations/{rr['id']}", headers=a).json()["status"] == "PENDING"


def test_expired_verification_link(client):
    data = form()
    client.post("/api/auth/register", data=data)
    token = last_token("/verify-email")
    with new_session() as db:
        row = db.scalars(
            select(ActionToken).where(ActionToken.token_hash == security.token_hash(token))
        ).one()
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
    r = client.post("/api/auth/verify-email", json={"token": token})
    assert r.status_code == 410 and r.json()["detail"] == "TOKEN_EXPIRED"  # UC-01 EX3


def test_reject_requires_reason_and_blocks_login(client):
    data = form()
    client.post("/api/auth/register", data=data)
    client.post("/api/auth/verify-email", json={"token": last_token("/verify-email")})
    a = auth(login(client, make_user(Role.ADMIN).email))
    rr = next(
        i
        for i in client.get("/api/admin/registrations", headers=a).json()["items"]
        if i["applicant"]["email"] == data["email"]
    )
    assert (
        client.post(f"/api/admin/registrations/{rr['id']}/reject", json={"reason": ""}, headers=a).status_code
        == 422
    )
    r = client.post(
        f"/api/admin/registrations/{rr['id']}/reject", json={"reason": "Мету не підтверджено"}, headers=a
    )
    assert r.json()["status"] == "REJECTED"
    r = client.post("/api/auth/login", json={"email": data["email"], "password": PASSWORD})
    assert r.status_code == 403 and r.json()["status"] == "REJECTED"


def test_admin_cannot_review_own_registration(client):
    admin = make_user(Role.ADMIN)
    from app.models.enums import AccountType, ReviewStatus
    from app.models.identity import RegistrationRequest

    with new_session() as db:
        rr = RegistrationRequest(
            user_id=admin.id,
            account_type=AccountType.INDIVIDUAL,
            purpose="власна заявка",
            status=ReviewStatus.PENDING,
        )
        db.add(rr)
        db.commit()
        rr_id = rr.id
    a = auth(login(client, admin.email))
    r = client.post(f"/api/admin/registrations/{rr_id}/approve", json={"role": "ANALYST"}, headers=a)
    assert r.status_code == 403 and r.json()["detail"] == "SELF_REVIEW_FORBIDDEN"  # D-01


# ---------------------------------------------------------------------------- UC-03, NFR-15, NFR-16
def test_wrong_password_and_lockout(client):
    user = make_user()
    for _ in range(4):
        r = client.post("/api/auth/login", json={"email": user.email, "password": "wrong-pass-1"})
        assert r.status_code == 401 and r.json()["detail"] == "INVALID_CREDENTIALS"
    unknown = client.post("/api/auth/login", json={"email": unique_email(), "password": "wrong-pass-1"})
    assert unknown.json() == {"detail": "INVALID_CREDENTIALS"}  # не видає, чи існує e-mail
    client.post("/api/auth/login", json={"email": user.email, "password": "wrong-pass-1"})  # 5-та
    r = client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    assert r.status_code == 423 and r.json()["retry_after"] > 800  # блок 15 хв навіть із правильним паролем
    assert audit_actions(user.id).count(("LOGIN_FAILED", "DENIED")) == 5


def test_role_is_checked_on_server_and_denial_is_audited(client):
    analyst = make_user()
    r = client.get("/api/admin/registrations", headers=auth(login(client, analyst.email)))
    assert r.status_code == 403 and r.json()["detail"] == "FORBIDDEN_ROLE"
    assert ("SECURITY_INCIDENT", "DENIED") in audit_actions(analyst.id)
    assert client.get("/api/me").status_code == 401
    assert (
        client.get("/api/me", headers={"Authorization": "Bearer forged"}).json()["detail"] == "TOKEN_EXPIRED"
    )


def test_refresh_rotation_and_csrf(client):
    user = make_user()
    login(client, user.email)
    csrf = client.cookies.get("gedh_csrf")
    old_refresh = client.cookies.get("gedh_refresh")
    assert client.post("/api/auth/refresh").json()["detail"] == "CSRF_FAILED"
    bad_origin = client.post(
        "/api/auth/refresh", headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"}
    )
    assert bad_origin.json()["detail"] == "CSRF_FAILED"
    r = client.post("/api/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and client.cookies.get("gedh_refresh") != old_refresh
    # старий токен оновлення після ротації не діє
    replay = TestClient(app, cookies={"gedh_refresh": old_refresh, "gedh_csrf": "x"})
    assert (
        replay.post("/api/auth/refresh", headers={"X-CSRF-Token": "x"}).json()["detail"] == "SESSION_REVOKED"
    )
    assert (
        client.post("/api/auth/logout", headers={"X-CSRF-Token": client.cookies.get("gedh_csrf")}).status_code
        == 204
    )
    assert client.get("/api/me", headers=auth(r.json())).json()["detail"] == "SESSION_REVOKED"


def test_login_rate_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    ratelimit.reset()
    codes = [
        client.post("/api/auth/login", json={"email": unique_email(), "password": "x"}).status_code
        for _ in range(11)
    ]
    assert codes[:10] == [401] * 10 and codes[10] == 429  # NFR-18: 10 спроб/хв з IP


# ---------------------------------------------------------------------------- UC-21
def test_profile_update_and_consent(client):
    user = make_user()
    s = auth(login(client, user.email))
    r = client.patch("/api/me", json={"full_name": "Нове Ім'я", "position": "аналітик"}, headers=s)
    assert r.json()["full_name"] == "Нове Ім'я"
    with new_session() as db:
        e = db.scalars(
            select(AuditEvent).where(
                AuditEvent.actor_id == user.id, AuditEvent.action == AuditAction.CONFIG_CHANGE
            )
        ).one()
        assert "Нове" not in e.details  # у журналі лише назви полів, не ПД
    assert client.post("/api/me/consent", json={"text_version": "0.1"}, headers=s).status_code == 400
    assert (
        client.post("/api/me/consent", json={"text_version": consent.CURRENT_VERSION}, headers=s).status_code
        == 201
    )


def test_delete_account(client):
    user = make_user()
    s = auth(login(client, user.email))
    body = {"password": "wrong-pass-1", "reason": "WITHDRAW_CONSENT", "confirm_data_deletion": True}
    assert client.post("/api/me/delete", json=body, headers=s).json()["detail"] == "PASSWORD_INCORRECT"
    r = client.post("/api/me/delete", json={**body, "password": PASSWORD}, headers=s)
    assert r.status_code == 202
    assert client.get("/api/me", headers=s).status_code == 401  # сесії відкликано одразу
    with new_session() as db:
        u = db.get(User, user.id)
        assert u.status == AccountStatus.DEACTIVATED and u.deactivated_at is not None
        actions = [
            r.action for r in db.scalars(select(ConsentRecord).where(ConsentRecord.user_id == user.id))
        ]
        assert ConsentAction.WITHDRAWN in actions
    assert ("ACCOUNT_DELETE", "SUCCESS") in audit_actions(user.id)
    r = client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    assert r.json()["status"] == "DEACTIVATED"


def test_reference_lists_are_public(client):
    from app.seed.reference_data import COUNTRIES

    # 25 країн платформи; адміністратор може додавати нові (UC-20 AC2) — перевіряємо входження
    assert {c[0] for c in COUNTRIES} <= {c["code"] for c in client.get("/api/countries").json()}
    assert {c["code"] for c in client.get("/api/categories").json()} >= {"PRICES", "TRADE"}
    assert all(o["name"] for o in client.get("/api/organizations").json())
    assert "email_domain" not in (client.get("/api/organizations").json() or [{}])[0]
    assert client.get("/api/auth/consent").json()["text_version"] == consent.CURRENT_VERSION


def test_audit_chain_is_intact(client):
    with new_session() as db:
        assert audit.verify_chain(db) is None  # DG-19: жоден запис не змінено
        assert db.scalar(select(AuditEvent.seq).where(AuditEvent.result == AuditResult.DENIED).limit(1))


def test_old_consent_suspends_access_until_new_version_accepted(client, monkeypatch):
    """DG-10 (S-39): зі старою версією згоди сервер пускає лише до профілю, згоди, копії даних і видалення.

    Записи згоди змінити не можна (тригер DG-10), тож «оновлюємо текст згоди» — підміною поточної версії.
    """
    u = make_user()
    h = auth(login(client, u.email))
    monkeypatch.setattr(consent, "CURRENT_VERSION", "9.9")
    r = client.get("/api/catalog/datasets", headers=h)
    assert r.status_code == 403 and r.json()["detail"] == "CONSENT_UPDATE_REQUIRED"
    assert client.get("/api/me", headers=h).status_code == 200
    assert client.get("/api/me/export", headers=h).status_code == 200
    ok = client.post("/api/me/consent", headers=h, json={"text_version": consent.CURRENT_VERSION})
    assert ok.status_code == 201
    assert client.get("/api/catalog/datasets", headers=h).status_code == 200
