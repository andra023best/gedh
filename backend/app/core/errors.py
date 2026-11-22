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


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(exc.body, status_code=exc.status, headers=exc.headers)
