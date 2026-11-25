"""Точка входу FastAPI. Усі маршрути — з префіксом /api (D-39: Vercel проксіює /api/* на Render)."""

import json
import logging
import re
import time
import uuid

from fastapi import APIRouter, FastAPI, Request

from app import __version__
from app.api import (
    admin,
    admin_config,
    analytics,
    audit,
    auth,
    data,
    diag,
    exchange,
    health,
    jobs,
    me,
    notifications,
    reference,
    uploads,
)
from app.core import errors
from app.core.config import get_settings
from app.core.proxy_guard import ProxyGuardMiddleware

# NFR-28: технічні журнали в stdout хостингу (Render зберігає їх сам); у шляху токени з листів маскуються
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
access_log = logging.getLogger("gedh.access")
TOKEN_IN_PATH = re.compile(r"(?<=/)[A-Za-z0-9_-]{40,}(?=/|$)")  # токени з листів — 43 символи


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="GEDH API",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    api = APIRouter(prefix="/api")
    api.include_router(health.router)
    api.include_router(auth.router)
    api.include_router(me.router)
    api.include_router(admin.router)
    api.include_router(admin_config.router)
    api.include_router(reference.router)
    api.include_router(data.router)
    api.include_router(analytics.router)
    api.include_router(jobs.router)
    api.include_router(uploads.router)
    api.include_router(exchange.router)
    api.include_router(audit.router)
    api.include_router(notifications.router)
    if settings.diag_enabled:
        api.include_router(diag.router)
    app.include_router(api)
    errors.install(app)

    @app.middleware("http")
    async def request_id_and_headers(request: Request, call_next):
        # Ідентифікатор запиту для журналу аудиту (UC-17): від Vercel або новий
        request.state.request_id = (request.headers.get("x-vercel-id") or str(uuid.uuid4()))[:64]
        started = time.perf_counter()
        response = await call_next(request)
        # NFR-28 (S-39): технічний журнал — JSON-рядок на запит, окремо від журналу аудиту;
        # без тіла, query й токенів
        access_log.info(
            json.dumps(
                {
                    "request_id": request.state.request_id,
                    "method": request.method,
                    "path": TOKEN_IN_PATH.sub("{token}", request.url.path),
                    "status": response.status_code,
                    "ms": round((time.perf_counter() - started) * 1000),
                },
                ensure_ascii=False,
            )
        )
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Cache-Control", "no-store")  # відповіді API з персональними даними
        return response

    app.add_middleware(ProxyGuardMiddleware, secret=settings.gedh_proxy_secret)
    return app


app = create_app()
