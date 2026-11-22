"""Точка входу FastAPI. Усі маршрути — з префіксом /api (D-39: Vercel проксіює /api/* на Render)."""

from fastapi import APIRouter, FastAPI

from app import __version__
from app.api import diag, health
from app.core.config import get_settings


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
    if settings.diag_enabled:
        api.include_router(diag.router)
    app.include_router(api)
    return app


app = create_app()
