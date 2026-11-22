"""Паролі (Argon2id, NFR-14), токени доступу (JWT 15 хв) і одноразові токени (NFR-15)."""

import hashlib
import logging
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

log = logging.getLogger("gedh.security")

ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=7)
LOCK_AFTER_FAILURES = 5
LOCK_DURATION = timedelta(minutes=15)

# Render Free має 512 МБ: 19 МіБ і 2 проходи — мінімум OWASP для Argon2id (~35 мс на хеш)
_hasher = PasswordHasher(memory_cost=19456, time_cost=2, parallelism=1)
# Хеш для порівняння, коли користувача не знайдено: час відповіді однаковий (не видає, чи існує e-mail)
_DUMMY_HASH = _hasher.hash("timing-equaliser-0")


def password_problems(password: str) -> list[str]:
    """NFR-14: щонайменше 10 символів, є літери й цифри."""
    problems = []
    if len(password) < 10:
        problems.append("PASSWORD_TOO_SHORT")
    if not re.search(r"[^\W\d_]", password):
        problems.append("PASSWORD_NEEDS_LETTER")
    if not re.search(r"\d", password):
        problems.append("PASSWORD_NEEDS_DIGIT")
    return problems


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


@lru_cache
def jwt_key() -> str:
    key = get_settings().jwt_secret
    if key:
        return key
    log.warning("JWT_SECRET не задано — тимчасовий ключ: сесії зникнуть після перезапуску")
    return secrets.token_hex(32)


def jwt_secret_is_ephemeral() -> bool:
    return not get_settings().jwt_secret


def create_access_token(user_id: uuid.UUID, role: str, session_id: uuid.UUID) -> str:
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "role": role, "sid": str(session_id), "iat": now, "exp": now + ACCESS_TTL}
    return jwt.encode(payload, jwt_key(), algorithm="HS256")


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, jwt_key(), algorithms=["HS256"], options={"require": ["sub", "exp", "sid"]})
    except jwt.PyJWTError:
        return None


def new_token() -> str:
    """Випадковий одноразовий токен (для листа чи cookie); у БД зберігаємо лише його SHA-256."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
