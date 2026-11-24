"""Помилки API: стабільний код у `detail` (контракт, розділ «Помилки»), текст перекладає інтерфейс."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, http_status: int, code: str, message: str | None = None, /, **extra) -> None:
        # позиційні параметри, щоб поле тіла `status` (статус об'єкта) не конфліктувало з HTTP-кодом
        self.status = http_status
        self.code = code
        self.body = {"detail": code, **({"message": message} if message else {}), **extra}
        self.headers = {"Retry-After": str(extra["retry_after"])} if "retry_after" in extra else None


def audit_denial(request: Request, exc: ApiError) -> None:
    """DG-06: кожна заборонена дія автентифікованого користувача — у журнал аудиту (S-38).

    Перевірки ролі (require, журнал аудиту) і доступу до даних (AccessPolicy) пишуть відмову самі й ставлять
    `request.state.denial_audited`; тут ловимо решту — перевірки «свій / чужий» усередині маршрутів
    (чужа заявка, погодження власної заявки D-01, неверифікована організація тощо). Окрема коротка транзакція:
    основна вже відкочена.
    """
    user = getattr(request.state, "user", None)
    if exc.status != 403 or user is None or getattr(request.state, "denial_audited", False):
        return
    from app.core import audit  # локально: уникаємо циклічного імпорту
    from app.core.db import new_session
    from app.models.enums import AuditAction, AuditResult

    with new_session() as db:
        audit.append(
            db,
            action=AuditAction.SECURITY_INCIDENT,
            result=AuditResult.DENIED,
            object_type="endpoint",
            object_id=f"{request.method} {request.url.path}"[:64],
            request_id=getattr(request.state, "request_id", None) or "-",
            actor_id=user.id,
            actor_role=user.role,
            on_behalf_of_id=user.organization_id,
            details={"reason": exc.code},
        )
        db.commit()


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        audit_denial(request, exc)
        return JSONResponse(exc.body, status_code=exc.status, headers=exc.headers)
