# ruff: noqa: F811 — фікстура world імпортується з test_exchange
"""S-42: заявник обирає конкретний набір; постачальник може дати старішу версію (дані з запізненням)."""

import os
import uuid

import pytest

from tests.test_exchange import new_request, world  # noqa: F401 — фікстура
from tests.test_uploads import SOURCE, months_ago

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="БД не налаштовано")


def upload(w, title: str, base: float, dataset_id: str | None = None) -> dict:
    """Постачальник завантажує 3 місяці значень у тестову категорію, DG-офіцер публікує."""
    c = w["c"]
    tag = uuid.uuid4().hex[:6]  # робить файл унікальним (захист від дублікатів за SHA-256)
    rows = "\n".join(f"UKR,R_CPI,Київ,{months_ago(n)},{base + n / 10},{tag}" for n in (3, 2, 1))
    form = {"category_code": w["cat"], **SOURCE}
    if dataset_id:
        form["dataset_id"] = dataset_id
    else:
        form |= {"title_ua": title, "title_en": title}
    files = {"file": ("p.csv", f"country,indicator,breakdown,period,value,note\n{rows}\n".encode())}
    v = c.post("/api/provider/uploads", headers=w["provider"], data=form, files=files).json()
    assert v["status"] == "PENDING_REVIEW", v
    assert c.post(f"/api/dataset-versions/{v['id']}/publish", headers=w["dg"]).status_code == 200
    return v


def test_requester_picks_dataset_when_category_has_several(world):
    w = world
    a = upload(w, "Ціни A", 100)["dataset_id"]
    b = upload(w, "Ціни B", 101)["dataset_id"]

    r = new_request(w)
    assert r.status_code == 422 and r.json()["detail"] == "DATASET_REQUIRED"
    assert set(r.json()["datasets"]) == {a, b}
    assert new_request(w, dataset_id=str(uuid.uuid4())).json()["detail"] == "DATASET_MISMATCH"

    ra = new_request(w, dataset_id=b)
    assert ra.status_code == 201 and ra.json()["target_dataset_id"] == b
    assert new_request(w, dataset_id=b).json()["detail"] == "DUPLICATE_ACTIVE_REQUEST"  # та сама пара
    assert new_request(w, dataset_id=a).status_code == 201  # інший набір тієї ж категорії — окрема заявка


def test_provider_pins_older_version(world):
    w, c = world, world["c"]
    v1 = upload(w, "Ціни із запізненням", 100)
    ds = v1["dataset_id"]
    upload(w, "", 103, dataset_id=ds)  # версія 2 — найновіша (103.x проти 100.x)

    rid = new_request(w).json()["id"]  # набір один — обирається сам
    assert c.post(
        f"/api/access-requests/{rid}/admin-decision", headers=w["admin"], json={"decision": "APPROVED"}
    )
    bad = c.post(
        f"/api/access-requests/{rid}/provider-decision",
        headers=w["provider"],
        json={"decision": "APPROVED", "version_no": 9},
    )
    assert bad.json()["detail"] == "UNKNOWN_VERSION"
    r = c.post(
        f"/api/access-requests/{rid}/provider-decision",
        headers=w["provider"],
        json={"decision": "APPROVED", "version_no": 1},
    )
    req = r.json()
    assert req["status"] == "AWAITING_AGREEMENT"
    assert req["decisions"][-1]["decision"] == "APPROVED_NARROWED"  # старіша версія — звуження обсягу
    assert req["decisions"][-1]["approved_version_no"] == 1

    dsa = c.get(f"/api/access-requests/{rid}/agreement", headers=w["rep"]).json()
    assert "версія 1 від" in dsa["text"] and "зафіксована постачальником" in dsa["text"]
    g = c.post(
        f"/api/access-requests/{rid}/agreement/accept", headers=w["rep"], json={"text_hash": dsa["text_hash"]}
    )
    assert g.status_code == 201 and g.json()["version_no"] == 1

    obs = c.get(f"/api/datasets/{ds}/observations", headers=w["rep"]).json()
    assert obs["version_no"] == 1  # хоча чинна — версія 2
    assert {int(i["value"]) for i in obs["items"]} == {100}  # значення версії 1 (100.x), а не 103.x
    assert (
        c.get(f"/api/datasets/{ds}/observations", headers=w["rep"], params={"version": 2}).status_code == 404
    )
    f = c.get(f"/api/datasets/{ds}/export", headers=w["rep"], params={"format": "json"})
    assert f.status_code == 200 and '"version_no": 1' in f.text
