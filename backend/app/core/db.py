"""Підключення до PostgreSQL (SQLAlchemy 2 + psycopg 3)."""

import time
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine | None:
    url = get_settings().sqlalchemy_url
    if url is None:
        return None
    # Render Free: 1 процес, 512 МБ — малий пул; pre_ping відновлює з'єднання після сну Neon.
    return create_engine(url, pool_size=2, max_overflow=2, pool_pre_ping=True)


def new_session() -> Session:
    engine = get_engine()
    if engine is None:
        raise RuntimeError("DATABASE_URL не задано")
    return sessionmaker(engine, expire_on_commit=False)()


def check_database() -> dict:
    """Стан БД для /api/health: ok | not_configured | error, час відповіді й версія сервера."""
    engine = get_engine()
    if engine is None:
        return {"status": "not_configured"}
    started = time.perf_counter()
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SHOW server_version")).scalar_one()
    except Exception as exc:  # noqa: BLE001 — health-check має повернути стан, а не впасти
        return {"status": "error", "error": type(exc).__name__}
    return {
        "status": "ok",
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "server_version": version,
    }
