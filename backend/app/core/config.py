"""Налаштування застосунку зі змінних середовища (NFR-16: секрети лише поза кодом)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"  # local | production
    # Рядок підключення Neon: postgresql://user:password@host/db?sslmode=require
    database_url: str | None = None
    # Діагностичні маршрути /api/diag/* для етапу Е0 (S-05); у продакшені пізніше вимкнути.
    diag_enabled: bool = True
    # Secure-атрибут cookie; локально по http має бути false.
    cookie_secure: bool = False
    frontend_base_url: str = "http://localhost:5173"
    # SQ-05 / S-08: секрет, який Vercel додає до кожного запиту /api/*. Якщо задано,
    # API відхиляє запити без нього (прямі звернення на Render в обхід Vercel).
    gedh_proxy_secret: str | None = None

    @property
    def sqlalchemy_url(self) -> str | None:
        """Neon видає URL зі схемою postgresql://, SQLAlchemy потрібен драйвер psycopg 3."""
        if not self.database_url:
            return None
        url = self.database_url
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
