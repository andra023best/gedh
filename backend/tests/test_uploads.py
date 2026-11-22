"""Етап Е3: завантаження закритих наборів і публікація DG-офіцером (UC-12, UC-13, UC-18; FR-21 – FR-23)."""

import json
import os
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.ingestion import upload
from app.main import app
from app.models.enums import Role
from app.seed.__main__ import sid
from tests.test_identity import auth, login, make_user

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="БД не налаштовано")

STAT_UKR = sid("org", "stat_ukr")
SOURCE = {
    "source_document_title": "Наказ про затвердження регіональних індексів (тест)",
    "source_methodology": "Вибіркове обстеження цін, методика 2024",
    "source_responsible_person": "Тест Відповідальний",
    "source_internal_ref": "TEST-REF",
}


@pytest.fixture
def client():
    return TestClient(app)


def months_ago(n: int) -> str:
    today = date.today()
    y, m = divmod(today.year * 12 + today.month - 1 - n, 12)
    return f"{y}-{m + 1:02d}"


# Періоди відносні до сьогодні: інакше правило Q-10 «актуальність» з часом законно дає попередження
PERIODS = {"m1": months_ago(1), "m2": months_ago(2), "y": str(date.today().year)}


def unique_csv(rows: list[str], header: str = ",".join(upload.CSV_COLUMNS)) -> bytes:
    # унікальний розріз робить кожен файл новим (інакше спрацює захист від дублікатів UC-12 EX3)
    body = "\n".join(r.format(**PERIODS) for r in rows)
    extra = f"UKR,R_CPI,тест-{uuid.uuid4().hex[:8]},{PERIODS['m1']},100.1,"
    return (header + "\n" + (body + "\n" if body else "") + extra + "\n").encode()


def post_upload(client, headers, data: bytes, name="data.csv", **form):
    fields = {
        "category_code": "PRICES",
        "title_ua": "Тестові ціни",
        "title_en": "Test prices",
        **SOURCE,
        **form,
    }
    return client.post("/api/provider/uploads", headers=headers, data=fields, files={"file": (name, data)})


@pytest.fixture
def provider(client):
    return auth(login(client, make_user(Role.DATA_PROVIDER, org_id=STAT_UKR).email))


@pytest.fixture
def dg(client):
    return auth(login(client, make_user(Role.DG_OFFICER).email))


def test_templates_are_public(client):
    csv_t = client.get("/api/templates/upload.csv")
    assert csv_t.status_code == 200 and "country,indicator,breakdown,period,value,note" in csv_t.text
    schema = client.get("/api/templates/upload.schema.json").json()
    assert schema["properties"]["observations"]["items"]["required"] == ["country", "indicator", "period"]


def test_clean_upload_goes_to_dg_and_gets_published(client, provider, dg):
    data = unique_csv(["UKR,R_CPI,Львівська,{m2},100.6,", "UKR,R_CPI,Львівська,{m1},100.9,"])
    r = post_upload(client, provider, data)
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["status"] == "PENDING_REVIEW" and v["is_synthetic"] and v["country_code"] == "UKR"  # D-04
    assert v["quality_report"]["critical_count"] == 0 and v["source"]["internal_ref"] == "TEST-REF"
    queue = client.get("/api/dg/review-queue", headers=dg).json()
    assert any(x["id"] == v["id"] for x in queue)
    pub = client.post(f"/api/dataset-versions/{v['id']}/publish", headers=dg)
    assert pub.status_code == 200 and pub.json()["status"] == "PUBLISHED"
    # нова версія того самого набору: попередня → SUPERSEDED, lineage (D-05)
    v2 = post_upload(
        client, provider, unique_csv(["UKR,R_CPI,Львівська,{m2},100.7,"]), dataset_id=v["dataset_id"]
    ).json()
    assert v2["version_no"] == 2
    detail = client.get(f"/api/dataset-versions/{v2['id']}", headers=dg).json()
    assert detail["diff"]["previous_version_no"] == 1 and detail["diff"]["changed"] >= 1
    client.post(f"/api/dataset-versions/{v2['id']}/publish", headers=dg)
    first = client.get(f"/api/dataset-versions/{v['id']}", headers=dg).json()
    assert first["status"] == "SUPERSEDED"
    assert (
        client.get(f"/api/dataset-versions/{v2['id']}", headers=dg).json()["previous_version_id"] == v["id"]
    )


def test_warnings_need_confirmation(client, provider, dg):
    data = unique_csv(["UKR,R_CPI,Одеська,{m2},130.0,"])  # 130 — поза типовим діапазоном 95–110 (Q-07)
    v = post_upload(client, provider, data).json()
    assert v["status"] == "QUARANTINE" and v["quality_report"]["warning_count"] >= 1
    assert all(x["id"] != v["id"] for x in client.get("/api/dg/review-queue", headers=dg).json())
    assert client.post(f"/api/dataset-versions/{v['id']}/publish", headers=dg).status_code == 409
    r = client.post(
        f"/api/dataset-versions/{v['id']}/confirm-warnings",
        headers=provider,
        json={"comment": "Ріст цін через подорожчання енергії — перевірено"},
    )
    assert r.json()["status"] == "PENDING_REVIEW" and r.json()["quality_report"]["warnings_confirmed_at"]


@pytest.mark.parametrize(
    ("rows", "rule"),
    [
        (["UKR,R_CPI,Київ,{m2},1500,"], "Q-06"),  # індекс 1500 — неможливо
        (["UKR,NOPE,Київ,{m2},100,"], "Q-03"),  # невідомий показник
        (["POL,R_CPI,Київ,{m2},100,"], "Q-03"),  # чужа країна
        (["UKR,R_CPI,Київ,{y},100,"], "Q-04"),  # рік для місячного показника
        (["UKR,R_CPI,Київ,{y}-13,100,"], "Q-01"),  # неіснуючий місяць — не за шаблоном
        (["UKR,R_CPI,Київ,{m2},abc,"], "Q-01"),  # значення не число
        (["UKR,R_CPI,Київ,{m2},100,", "UKR,R_CPI,Київ,{m2},101,"], "Q-05"),  # повтор
    ],
)
def test_critical_issues_reject_version(client, provider, rows, rule):
    v = post_upload(client, provider, unique_csv(rows)).json()
    assert v["status"] == "REJECTED" and v["records"] == 0  # значення відхиленої версії не зберігаються
    assert rule in {i["rule_code"] for i in v["quality_report"]["issues"] if i["severity"] == "CRITICAL"}


def test_wrong_header_is_q01(client, provider):
    v = post_upload(
        client, provider, unique_csv(["UKR,R_CPI,{m2},100"], header="country,indicator,period,value")
    )
    assert v.json()["status"] == "REJECTED"
    assert v.json()["quality_report"]["issues"][0]["rule_code"] == "Q-01"


def test_json_upload(client, provider):
    doc = {
        "observations": [
            {
                "country": "UKR",
                "indicator": "R_CPI",
                "breakdown": f"json-{uuid.uuid4().hex[:6]}",
                "period": PERIODS["m1"],
                "value": 100.4,
            }
        ]
    }
    r = post_upload(client, provider, json.dumps(doc).encode(), name="data.json")
    assert r.status_code == 201 and r.json()["status"] == "PENDING_REVIEW"


def test_source_required_duplicate_and_file_type(client, provider):
    data = unique_csv(["UKR,R_CPI,Київ,{m2},100,"])
    no_source = post_upload(client, provider, data, source_internal_ref="", source_methodology="")
    assert no_source.status_code == 422 and no_source.json()["detail"] == "SOURCE_REQUIRED"  # Q-11, UC-12 EX1
    assert post_upload(client, provider, data).status_code == 201
    dup = post_upload(client, provider, data)
    assert dup.status_code == 409 and dup.json()["detail"] == "DUPLICATE_FILE"  # UC-12 EX3
    xlsx = post_upload(client, provider, b"PK\x03\x04 excel", name="data.xlsx")
    assert xlsx.status_code == 415  # NFR-19: тип за вмістом
    big = post_upload(client, provider, b"a" * (5 * 1024 * 1024 + 1))
    assert big.status_code == 413


def test_roles_and_ownership(client, provider, dg):
    analyst = auth(login(client, make_user().email))
    assert post_upload(client, analyst, unique_csv([])).status_code == 403
    other = auth(login(client, make_user(Role.DATA_PROVIDER, org_id=sid("org", "trade_pol")).email))
    v = post_upload(client, provider, unique_csv(["UKR,R_CPI,Київ,{m2},100,"])).json()
    assert client.get(f"/api/dataset-versions/{v['id']}", headers=other).status_code == 403
    r = post_upload(client, other, unique_csv([]), dataset_id=v["dataset_id"])
    assert r.status_code == 403  # чужий набір оновлювати не можна
    assert client.post(f"/api/dataset-versions/{v['id']}/publish", headers=provider).status_code == 403
    rej = client.post(
        f"/api/dataset-versions/{v['id']}/reject", headers=dg, json={"reason": "Не підтверджено"}
    )
    assert rej.json()["status"] == "REJECTED" and rej.json()["rejection_reason"] == "Не підтверджено"
    mine = client.get("/api/provider/datasets", headers=provider).json()
    assert all(d["country_code"] == "UKR" for d in mine)


def test_period_parsing():
    assert upload.parse_period("2025-Q2") == (upload.date(2025, 4, 1), upload.date(2025, 6, 30))
    assert upload.parse_period("2024-02") == (upload.date(2024, 2, 1), upload.date(2024, 2, 29))
    assert upload.parse_period("2023") == (upload.date(2023, 1, 1), upload.date(2023, 12, 31))
