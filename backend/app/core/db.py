"""Підключення до PostgreSQL (SQLAlchemy 2 + psycopg 3).

DG-19: кожна транзакція застосунку виконується від імені ролі `gedh_app` (SET LOCAL ROLE), яка не має
прав UPDATE/DELETE на журнал аудиту (міграція 0006). SET LOCAL діє до кінця транзакції, тож це працює
і з пулером з'єднань Neon у режимі транзакцій. Міграції (alembic) мають власне з'єднання без цієї ролі.
"""

import logging
import re
import time
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

log = logging.getLogger("gedh.db")
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _usable_app_role(engine: Engine, role: str) -> str | None:
    """Роль, якою можна користуватися: існує, і поточний користувач має на неї право SET (PostgreSQL 16+)."""
    if not role or not _ROLE_NAME.match(role):
        return None
    try:
        with engine.connect() as conn:
            ok = conn.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :r) "
                    "AND pg_has_role(current_user, :r, 'SET')"
                ),
                {"r": role},
            ).scalar_one()
    except Exception as exc:  # noqa: BLE001 — БД ще спить або роль не створено: працюємо без неї
        log.warning("db app role check failed: %s", type(exc).__name__)
        return None
    if not ok:
        log.warning("db app role %s is not available; transactions run as the connecting user", role)
    return role if ok else None


@lru_cache
def get_engine() -> Engine | None:
    url = get_settings().sqlalchemy_url
    if url is None:
        return None
    # Render Free: 1 процес, 512 МБ — малий пул; pre_ping відновлює з'єднання після сну Neon.
    engine = create_engine(url, pool_size=2, max_overflow=2, pool_pre_ping=True)
    role = _usable_app_role(engine, get_settings().db_app_role)
    if role:

        @event.listens_for(engine, "begin")
        def _set_app_role(conn) -> None:  # noqa: ANN001 — сигнатура події SQLAlchemy
            conn.exec_driver_sql(f"SET LOCAL ROLE {role}")

    return engine


def new_session() -> Session:
    engine = get_engine()
    if engine is None:
        raise RuntimeError("DATABASE_URL не задано")
    return sessionmaker(engine, expire_on_commit=False)()


def check_database() -> dict:
    """Стан БД для /api/health: ok | not_configured | error, час відповіді, версія й роль застосунку."""
    engine = get_engine()
    if engine is None:
        return {"status": "not_configured"}
    started = time.perf_counter()
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SHOW server_version")).scalar_one()
            role = conn.execute(text("SELECT current_user")).scalar_one()
    except Exception as exc:  # noqa: BLE001 — health-check має повернути стан, а не впасти
        return {"status": "error", "error": type(exc).__name__}
    return {
        "status": "ok",
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "server_version": version,
        # DG-19: «gedh_app» — транзакції йдуть від імені ролі без прав змінювати журнал аудиту
        "app_role": role,
    }
