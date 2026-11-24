"""Залежності FastAPI: сесія БД, ідентифікатор запиту, поточний користувач і перевірка ролі (NFR-16).

Права перевіряються на сервері під час кожного запиту, за замовчуванням доступ заборонено.
Роль і статус беруться з БД, а не з токена, тож деактивація й зміна ролі діють одразу.
"""

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core import audit, ratelimit
from app.core.db import new_session
from app.core.errors import ApiError
from app.core.security import decode_access_token, token_hash
from app.models.enums import AccountStatus, AuditAction, AuditResult, Role
from app.models.identity import ApiKey, User
from app.models.identity import Session as UserSession


def get_db() -> Iterator[Session]:
    db = new_session()
    try:
        yield db
    finally:
        db.close()  # незафіксовані зміни відкочуються


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid.uuid4())


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise ApiError(401, "NOT_AUTHENTICATED")
    claims = decode_access_token(header[7:].strip())
    if claims is None:
        raise ApiError(401, "TOKEN_EXPIRED")
    user = db.get(User, uuid.UUID(claims["sub"]))
    session = db.get(UserSession, uuid.UUID(claims["sid"]))
    now = datetime.now(UTC)
    if user is None or session is None or session.user_id != user.id:
        raise ApiError(401, "NOT_AUTHENTICATED")
    if session.revoked_at is not None or session.expires_at <= now:
        raise ApiError(401, "SESSION_REVOKED")
    if user.status != AccountStatus.ACTIVE:
        raise ApiError(401, "ACCOUNT_NOT_ACTIVE", status=user.status.value)
    request.state.user = user
    request.state.session_id = session.id
    return user


API_KEY_HEADER = "x-api-key"


def user_by_api_key(request: Request, db: Session, key: str) -> User:
    """FR-12 / FR-37: ключ `gedh_<префікс>_<секрет>`; у БД — лише префікс і SHA-256 (NFR-15, NFR-20)."""
    parts = key.strip().split("_", 2)  # у секреті теж можуть бути «_»
    if len(parts) != 3 or parts[0] != "gedh":
        raise ApiError(401, "API_KEY_INVALID")
    row = db.query(ApiKey).filter(ApiKey.prefix == f"gedh_{parts[1]}").first()
    if row is None or row.revoked_at is not None or row.key_hash != token_hash(key.strip()):
        raise ApiError(401, "API_KEY_INVALID")
    ratelimit.check_key(row.prefix)  # NFR-18: 60 запитів за хвилину на ключ
    user = db.get(User, row.user_id)
    if user is None or user.status != AccountStatus.ACTIVE:
        raise ApiError(401, "ACCOUNT_NOT_ACTIVE", status=user.status.value if user else None)
    row.last_used_at = datetime.now(UTC)
    db.commit()
    request.state.user = user
    request.state.channel = "API"
    return user


def api_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Сесія браузера (Bearer) **або** API-ключ (`X-API-Key`).

    Ключ приймають лише маршрути читання даних і завантаження наборів постачальником (S-37); профіль, заявки,
    погодження й адміністрування — тільки через сесію.
    """
    if request.headers.get("authorization", "").lower().startswith("bearer "):
        return current_user(request, db)
    key = request.headers.get(API_KEY_HEADER)
    if key:
        return user_by_api_key(request, db, key)
    raise ApiError(401, "NOT_AUTHENTICATED")


def require_api(*roles: Role) -> Callable[..., User]:
    """Як require(), але приймає й API-ключ (для завантаження наборів постачальником, FR-37)."""

    def checker(request: Request, user: User = Depends(api_user), db: Session = Depends(get_db)) -> User:
        return _check_role(request, user, db, roles)

    return checker


def _check_role(request: Request, user: User, db: Session, roles: tuple[Role, ...]) -> User:
    if user.role not in roles:
        request.state.denial_audited = True  # записано тут, обробник помилок не дублює
        audit.append(
            db,
            action=AuditAction.SECURITY_INCIDENT,
            result=AuditResult.DENIED,
            object_type="endpoint",
            object_id=f"{request.method} {request.url.path}"[:64],
            request_id=request_id(request),
            actor_id=user.id,
            actor_role=user.role,
            details={"reason": "FORBIDDEN_ROLE"},
        )
        db.commit()
        raise ApiError(403, "FORBIDDEN_ROLE")
    return user


def require(*roles: Role) -> Callable[..., User]:
    """Дозволяє лише вказані ролі; спробу іншої ролі записує в журнал аудиту (DENIED)."""

    def checker(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
        return _check_role(request, user, db, roles)

    return checker
