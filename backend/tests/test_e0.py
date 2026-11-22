"""Тести етапу Е0: health-check і діагностика проксі."""

import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"]["status"] in {"ok", "not_configured"}


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="БД не налаштовано")
def test_health_database_reachable():
    db = client.get("/api/health").json()["db"]
    assert db["status"] == "ok"
    assert db["server_version"].startswith("17")


def test_cookie_round_trip():
    c = TestClient(app)
    value = c.post("/api/diag/cookie").json()["set"]
    assert c.get("/api/diag/cookie").json() == {"received": True, "value": value}


def test_upload_hash_and_limit():
    r = client.post("/api/diag/upload", files={"file": ("a.csv", b"country,value\nUA,1\n")})
    assert r.status_code == 200
    assert r.json()["size"] == 19
    too_big = b"x" * (5 * 1024 * 1024 + 1)
    r = client.post("/api/diag/upload", files={"file": ("big.csv", too_big)})
    assert r.status_code == 413
    assert r.json()["detail"] == "FILE_TOO_LARGE"
