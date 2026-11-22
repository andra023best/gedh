"""Залежності FastAPI: сесія БД, ідентифікатор запиту, поточний користувач і перевірка ролі (NFR-16).

Права перевіряються на сервері під час кожного запиту, за замовчуванням доступ заборонено.
Роль і статус беруться з БД, а не з токена, тож деактивація й зміна ролі діють одразу.
"""

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core import audit
from app.core.db import new_session
from app.core.errors import ApiError
from app.core.security import decode_access_token
from app.models.enums import AccountStatus, AuditAction, AuditResult, Role
from app.models.identity import Session as UserSession
from app.models.identity import User


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


def require(*roles: Role) -> Callable[..., User]:
    """Дозволяє лише вказані ролі; спробу іншої ролі записує в журнал аудиту (DENIED)."""

    def checker(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
        if user.role not in roles:
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

    return checker
