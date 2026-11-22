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
    # Підпис JWT (NFR-20: лише в змінних середовища). Render генерує значення сам (render.yaml).
    jwt_secret: str | None = None
    # Лист через Brevo (HTTP API). Без ключа листи лише пишуться в журнал сервера (локально й у тестах).
    brevo_api_key: str | None = None
    mail_from: str = "noreply@gedh.invalid"
    mail_from_name: str = "GEDH"
    # Пароль демо-облікових записів (*@demo.gedh.invalid); без нього демо-вхід вимкнено.
    demo_password: str | None = None
    rate_limit_enabled: bool = True
    # Секрет службових задач (GitHub Actions → /api/internal/jobs/*).
    job_token: str | None = None

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
