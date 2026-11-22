"""Захист від звернень до API в обхід Vercel (SQ-05, S-08).

Vercel додає до кожного запиту /api/* заголовок X-GEDH-Proxy-Secret зі значенням зі своєї
змінної середовища (vercel.json → routes → transforms). Якщо запит прийшов напряму на Render,
заголовка немає, і API відповідає 403. Виняток — /api/health: його викликає Render для перевірки живості.
Поки GEDH_PROXY_SECRET не задано (локальна розробка, тести), перевірка вимкнена.
"""

import hmac

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

HEADER = "x-gedh-proxy-secret"
OPEN_PATHS = frozenset({"/api/health"})


class ProxyGuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, secret: str | None) -> None:
        super().__init__(app)
        self.secret = secret.encode() if secret else None

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if self.secret and request.url.path not in OPEN_PATHS:
            sent = request.headers.get(HEADER, "").encode()
            if not hmac.compare_digest(sent, self.secret):
                return JSONResponse({"detail": "DIRECT_ACCESS_FORBIDDEN"}, status_code=403)
        return await call_next(request)


def client_ip(request: Request) -> str | None:
    """Справжня IP клієнта — лише із заголовка, який Vercel перезаписує (S-07).

    request.client.host не використовувати: його можна підробити через X-Forwarded-For.
    """
    return request.headers.get("x-vercel-forwarded-for")
