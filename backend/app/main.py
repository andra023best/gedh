"""Точка входу FastAPI. Усі маршрути — з префіксом /api (D-39: Vercel проксіює /api/* на Render)."""

import uuid

from fastapi import APIRouter, FastAPI, Request

from app import __version__
from app.api import admin, auth, data, diag, health, jobs, me, reference
from app.core import errors
from app.core.config import get_settings
from app.core.proxy_guard import ProxyGuardMiddleware


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
    api.include_router(reference.router)
    api.include_router(data.router)
    api.include_router(jobs.router)
    if settings.diag_enabled:
        api.include_router(diag.router)
    app.include_router(api)
    errors.install(app)

    @app.middleware("http")
    async def request_id_and_headers(request: Request, call_next):
        # Ідентифікатор запиту для журналу аудиту (UC-17): від Vercel або новий
        request.state.request_id = (request.headers.get("x-vercel-id") or str(uuid.uuid4()))[:64]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Cache-Control", "no-store")  # відповіді API з персональними даними
        return response

    app.add_middleware(ProxyGuardMiddleware, secret=settings.gedh_proxy_secret)
    return app


app = create_app()
