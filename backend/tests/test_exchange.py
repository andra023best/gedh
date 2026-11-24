"""Етап Е4: обмін закритими даними (UC-08 – UC-11, UC-16, UC-19; FR-18 – FR-20, FR-24 – FR-28, FR-44, FR-45).

Кожен тест створює власну категорію даних і організацію-заявника,
тож тести не залежать від даних попередніх запусків.
"""

import hashlib
import os
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.data import csv_safe
from app.api.jobs import expire_grants
from app.core.db import new_session
from app.main import app
from app.models.enums import OrganizationType, Role, VerificationStatus
from app.models.exchange import AccessGrant, DataSharingAgreement
from app.models.identity import Organization
from app.models.reference import DataCategory
from app.seed.__main__ import sid
from tests.test_identity import auth, login, make_user
from tests.test_uploads import SOURCE, months_ago

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="БД не налаштовано")
STAT_UKR = sid("org", "stat_ukr")


def month_bounds(ym: str) -> tuple[str, str]:
    y, m = map(int, ym.split("-"))
    first = date(y, m, 1)
    last = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    return first.isoformat(), last.isoformat()


@pytest.fixture
def world():
    """Нова категорія, організація-заявник, представник, адміністратор, постачальник UKR, DG-офіцер."""
    code = f"T{uuid.uuid4().hex[:8].upper()}"
    with new_session() as db:
        db.add(DataCategory(code=code, name_ua=f"Тестова категорія {code}", name_en=f"Test {code}"))
        org = Organization(
            name=f"Агенція {code} (тест)",
            type=OrganizationType.ANALYTICS_COMPANY,
            country_code="UKR",
            status=VerificationStatus.VERIFIED,
            verified_at=datetime.now(UTC),
        )
        db.add(org)
        db.commit()
        org_id = org.id
    c = TestClient(app)
    return {
        "c": c,
        "cat": code,
        "org": org_id,
        "rep": auth(login(c, make_user(Role.ORG_REPRESENTATIVE, org_id=org_id).email)),
        "admin": auth(login(c, make_user(Role.ADMIN).email)),
        "provider": auth(login(c, make_user(Role.DATA_PROVIDER, org_id=STAT_UKR).email)),
        "dg": auth(login(c, make_user(Role.DG_OFFICER).email)),
    }


def new_request(w, **over):
    m3, m1 = months_ago(3), months_ago(1)
    body = {
        "country_code": "UKR",
        "category_code": w["cat"],
        "period": {"start": month_bounds(m3)[0], "end": month_bounds(m1)[1]},
        "purpose": "Аналіз регіональних цін для дослідження",
        "usage_place": "Київ, офіс агенції",
        "requested_term_days": 90,
        "responsible_person": "Олена Тестова",
        **over,
    }
    return w["c"].post("/api/access-requests", headers=w["rep"], json=body)


def publish_dataset(w) -> str:
    """Постачальник завантажує набір у тестову категорію (3 місяці), DG-офіцер публікує."""
    # код категорії в примітці робить файл унікальним (інакше спрацює захист від дублікатів за SHA-256)
    rows = "\n".join(f"UKR,R_CPI,Київ,{months_ago(n)},{100 + n / 10},{w['cat']}" for n in (3, 2, 1))
    csv = f"country,indicator,breakdown,period,value,note\n{rows}\n".encode()
    form = {
        "category_code": w["cat"],
        "title_ua": f"Ціни {w['cat']}",
        "title_en": f"Prices {w['cat']}",
        **SOURCE,
    }
    v = (
        w["c"]
        .post("/api/provider/uploads", headers=w["provider"], data=form, files={"file": ("p.csv", csv)})
        .json()
    )
    assert v["status"] == "PENDING_REVIEW", v
    assert w["c"].post(f"/api/dataset-versions/{v['id']}/publish", headers=w["dg"]).status_code == 200
    return v["dataset_id"]


def test_full_exchange_chain(world):
    w, c = world, world["c"]
    # UC-08: заявка; набору ще немає (UC-08 AC1)
    r = new_request(w)
    assert r.status_code == 201, r.text
    req = r.json()
    assert (
        req["status"] == "SUBMITTED"
        and req["number"].startswith("GEDH-")
        and req["target_dataset_id"] is None
    )
    rid = req["id"]
    # постачальник не бачить заявку до першого погодження (UC-10 EX2)
    assert c.get(f"/api/access-requests/{rid}", headers=w["provider"]).status_code == 403

    # UC-09: уточнення → відповідь → погодження адміністратором
    assert (
        c.post(
            f"/api/access-requests/{rid}/admin-decision",
            headers=w["admin"],
            json={"decision": "NEEDS_CLARIFICATION"},
        ).status_code
        == 422
    )  # причина обов'язкова
    r = c.post(
        f"/api/access-requests/{rid}/admin-decision",
        headers=w["admin"],
        json={"decision": "NEEDS_CLARIFICATION", "reason": "Уточніть місце використання"},
    )
    assert r.json()["status"] == "NEEDS_CLARIFICATION"
    r = c.post(
        f"/api/access-requests/{rid}/clarification", headers=w["rep"], json={"answer": "Лише офіс у Києві"}
    )
    assert r.json()["status"] == "SUBMITTED"
    r = c.post(
        f"/api/access-requests/{rid}/admin-decision", headers=w["admin"], json={"decision": "APPROVED"}
    )
    assert r.json()["status"] == "PROVIDER_REVIEW"

    # UC-10: постачальник іншої країни — ні; ширше за запит — ні; звужує — так
    pol = auth(login(c, make_user(Role.DATA_PROVIDER, org_id=sid("org", "trade_pol")).email))
    assert (
        c.post(
            f"/api/access-requests/{rid}/provider-decision", headers=pol, json={"decision": "APPROVED"}
        ).status_code
        == 403
    )
    m2, m1 = months_ago(2), months_ago(1)
    wider = {"decision": "APPROVED", "approved_period": {"start": "2000-01-01", "end": month_bounds(m1)[1]}}
    assert (
        c.post(f"/api/access-requests/{rid}/provider-decision", headers=w["provider"], json=wider).json()[
            "detail"
        ]
        == "SCOPE_NOT_NARROWER"
    )
    narrow = {
        "decision": "APPROVED",
        "approved_period": {"start": month_bounds(m2)[0], "end": month_bounds(m1)[1]},
        "approved_term_days": 30,
    }
    r = c.post(f"/api/access-requests/{rid}/provider-decision", headers=w["provider"], json=narrow)
    assert r.json()["status"] == "AWAITING_DATA"  # набору ще немає
    assert r.json()["decisions"][-1]["decision"] == "APPROVED_NARROWED"

    # UC-12 / UC-13: постачальник завантажує, DG-офіцер публікує → заявці пропонується угода
    dataset_id = publish_dataset(w)
    req = c.get(f"/api/access-requests/{rid}", headers=w["rep"]).json()
    assert req["status"] == "AWAITING_AGREEMENT" and req["target_dataset_id"] == dataset_id

    # UC-11: угода з погодженим (звуженим) обсягом; акцепт лише з хешем показаного тексту
    dsa = c.get(f"/api/access-requests/{rid}/agreement", headers=w["rep"]).json()
    assert dsa["scope"]["narrowed"] and dsa["scope"]["term_days"] == 30
    assert "не передавати дані третім особам" in dsa["text"]
    assert dsa["text_hash"] == hashlib.sha256(dsa["text"].encode()).hexdigest()
    bad = c.post(
        f"/api/access-requests/{rid}/agreement/accept", headers=w["rep"], json={"text_hash": "0" * 64}
    )
    assert bad.json()["detail"] == "AGREEMENT_TEXT_CHANGED"
    g = c.post(
        f"/api/access-requests/{rid}/agreement/accept", headers=w["rep"], json={"text_hash": dsa["text_hash"]}
    )
    assert g.status_code == 201
    grant = g.json()
    assert grant["status"] == "ACTIVE" and grant["period"]["start"] == month_bounds(m2)[0]

    # UC-16: доступ лише в межах дозволу — місяць m3 поза дозволом (часткове покриття, UC-16 AC1)
    obs = c.get(f"/api/datasets/{dataset_id}/observations", headers=w["rep"]).json()
    assert obs["grant_id"] == grant["id"] and obs["coverage"] == "PARTIAL" and obs["is_synthetic"]
    assert {i["period"]["start"][:7] for i in obs["items"]} == {m2, m1}
    # інший користувач без дозволу — відмова
    stranger = auth(login(c, make_user().email))
    assert (
        c.get(f"/api/datasets/{dataset_id}/observations", headers=stranger).json()["detail"]
        == "ACCESS_DENIED"
    )

    # UC-07: вивантаження — метадані, позначка, дозвіл, SHA-256 у заголовку = хеш тіла
    f = c.get(f"/api/datasets/{dataset_id}/export?format=csv", headers=w["rep"])
    assert f.status_code == 200 and f.headers["x-grant-id"] == grant["id"]
    assert f.headers["x-content-sha256"] == hashlib.sha256(f.content).hexdigest()
    text = f.content.decode("utf-8-sig")
    assert (
        "СИНТЕТИЧНІ ДАНІ" in text
        and f"# grant_id: {grant['id']}" in text
        and "attachment" in f.headers["content-disposition"]
    )
    j = c.get(f"/api/datasets/{dataset_id}/export?format=json", headers=w["rep"]).json()
    assert j["metadata"]["grant_id"] == grant["id"] and len(j["observations"]) == 2

    # UC-19: строк минув — відмова одразу, ще до щоденної задачі (DG-05); задача позначає EXPIRED
    with new_session() as db:
        row = db.get(AccessGrant, uuid.UUID(grant["id"]))
        row.valid_from, row.valid_to = (
            datetime.now(UTC) - timedelta(days=31),
            datetime.now(UTC) - timedelta(minutes=1),
        )
        db.commit()
    r = c.get(f"/api/datasets/{dataset_id}/observations", headers=w["rep"])
    assert r.status_code == 403 and r.json()["detail"] == "GRANT_EXPIRED"
    assert expire_grants("test")["expired"] >= 1
    assert c.get(f"/api/grants/{grant['id']}", headers=w["rep"]).json()["status"] == "EXPIRED"
    assert c.get(f"/api/access-requests/{rid}", headers=w["rep"]).json()["status"] == "EXPIRED"

    # DG-12 / FR-45: нова заявка — лише після підтвердження видалення (FR-44)
    blocked = new_request(w)
    assert blocked.status_code == 409 and blocked.json()["detail"] == "DELETION_CONFIRMATION_REQUIRED"
    ok = c.post(f"/api/grants/{grant['id']}/confirm-deletion", headers=w["rep"])
    assert ok.json()["deletion_confirmed_at"]
    assert new_request(w).status_code == 201


def test_request_validation_and_roles(world):
    w, c = world, world["c"]
    assert new_request(w, requested_term_days=181).status_code == 422  # строк ≤ 180 днів
    no_provider = new_request(w, country_code="USA")
    assert no_provider.json()["detail"] == "NO_PROVIDER_FOR_COUNTRY"  # UC-08 EX3
    analyst = auth(login(c, make_user().email))
    assert c.post("/api/access-requests", headers=analyst, json={}).status_code == 403  # лише представник
    first = new_request(w)
    dup = new_request(w)
    assert dup.status_code == 409 and dup.json()["existing_id"] == first.json()["id"]  # UC-08 EX2


def test_admin_of_same_organization_cannot_decide(world):
    w, c = world, world["c"]
    rid = new_request(w).json()["id"]
    same_org_admin = auth(login(c, make_user(Role.ADMIN, org_id=w["org"]).email))
    r = c.post(
        f"/api/access-requests/{rid}/admin-decision", headers=same_org_admin, json={"decision": "APPROVED"}
    )
    assert r.status_code == 403 and r.json()["detail"] == "SELF_REVIEW_FORBIDDEN"  # DG-07, UC-09 EX3


def test_rejection_and_offer_expiry(world):
    w, c = world, world["c"]
    rid = new_request(w).json()["id"]
    r = c.post(
        f"/api/access-requests/{rid}/admin-decision",
        headers=w["admin"],
        json={"decision": "REJECTED", "reason": "Мету не підтверджено"},
    )
    assert r.json()["status"] == "REJECTED" and r.json()["decisions"][0]["reason"] == "Мету не підтверджено"

    publish_dataset(w)  # набір є → після погоджень одразу угода
    rid = new_request(w).json()["id"]
    c.post(f"/api/access-requests/{rid}/admin-decision", headers=w["admin"], json={"decision": "APPROVED"})
    r = c.post(
        f"/api/access-requests/{rid}/provider-decision", headers=w["provider"], json={"decision": "APPROVED"}
    )
    assert r.json()["status"] == "AWAITING_AGREEMENT"
    with new_session() as db:
        dsa = db.scalars(
            select(DataSharingAgreement).where(DataSharingAgreement.request_id == uuid.UUID(rid))
        ).one()
        dsa.offered_at = datetime.now(UTC) - timedelta(days=15)  # пропозиція старша за 14 днів (UC-11 EX2)
        db.commit()
    assert expire_grants("test")["offers_cancelled"] >= 1
    assert c.get(f"/api/access-requests/{rid}", headers=w["rep"]).json()["status"] == "CANCELLED"


def test_grants_list_scopes(world):
    w, c = world, world["c"]
    assert c.get("/api/grants", headers=w["rep"]).status_code == 200
    analyst = auth(login(c, make_user().email))
    assert c.get("/api/grants", headers=analyst).status_code == 403


def test_csv_injection_is_neutralised():
    assert csv_safe('=HYPERLINK("http://x")').startswith("'=")
    assert csv_safe("+380") == "'+380" and csv_safe("@cmd") == "'@cmd" and csv_safe("Київ") == "Київ"
    assert csv_safe(None) == ""
