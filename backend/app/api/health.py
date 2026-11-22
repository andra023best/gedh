"""GET /api/health — перевірка живості для Render і «пробудження» сервера з інтерфейсу."""

from datetime import UTC, datetime

from fastapi import APIRouter

from app import __version__
from app.core.config import get_settings
from app.core.db import check_database
from app.core.security import jwt_secret_is_ephemeral

router = APIRouter(tags=["service"])

STARTED_AT = datetime.now(UTC)


@router.get("/health")
def health() -> dict:
    now = datetime.now(UTC)
    return {
        "status": "ok",
        "version": __version__,
        "env": get_settings().app_env,
        "started_at": STARTED_AT.isoformat(),
        # Мале значення uptime після паузи означає, що Render щойно «прокинувся» (холодний старт).
        "uptime_s": round((now - STARTED_AT).total_seconds()),
        "db": check_database(),
        # Попередження конфігурації (без значень секретів)
        "warnings": [
            w
            for w, bad in (
                ("JWT_SECRET_EPHEMERAL", jwt_secret_is_ephemeral()),
                ("MAIL_DISABLED", not get_settings().brevo_api_key),
            )
            if bad
        ],
    }
