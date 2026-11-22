"""SQ-05 / S-08: API приймає лише запити, що пройшли через Vercel (із секретним заголовком)."""

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


def make_client(monkeypatch, secret: str | None) -> TestClient:
    if secret:
        monkeypatch.setenv("GEDH_PROXY_SECRET", secret)
    else:
        monkeypatch.delenv("GEDH_PROXY_SECRET", raising=False)
    get_settings.cache_clear()
    try:
        return TestClient(create_app())
    finally:
        get_settings.cache_clear()


def test_without_secret_configured_everything_is_open(monkeypatch):
    c = make_client(monkeypatch, None)
    assert c.get("/api/diag/request").status_code == 200


def test_direct_request_is_rejected(monkeypatch):
    c = make_client(monkeypatch, "s3cret")
    r = c.get("/api/diag/request")
    assert r.status_code == 403
    assert r.json()["detail"] == "DIRECT_ACCESS_FORBIDDEN"
    assert c.get("/api/diag/request", headers={"X-GEDH-Proxy-Secret": "wrong"}).status_code == 403


def test_request_through_proxy_is_allowed(monkeypatch):
    c = make_client(monkeypatch, "s3cret")
    assert c.get("/api/diag/request", headers={"X-GEDH-Proxy-Secret": "s3cret"}).status_code == 200


def test_health_stays_open_for_render(monkeypatch):
    c = make_client(monkeypatch, "s3cret")
    assert c.get("/api/health").status_code == 200
