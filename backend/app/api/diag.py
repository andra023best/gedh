"""Діагностика етапу Е0 (S-05): чи проходять cookie, заголовки й файли через проксі Vercel.

Ризики з `02_Нотатки_для_розробки.md` (розд. 3, 6), які тут перевіряються:
- cookie токена оновлення має бути host-only і доходити до API через rewrites;
- реальна IP клієнта — у `x-vercel-forwarded-for`, а не в `x-forwarded-for`;
- файл до 5 МБ має проходити через проксі.
Маршрути вмикаються змінною DIAG_ENABLED і будуть вимкнені після Е0.
"""

import hashlib
import secrets

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile

from app.core.config import get_settings

router = APIRouter(prefix="/diag", tags=["diagnostics"])

COOKIE_NAME = "gedh_diag"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # NFR-18: файл набору ≤ 5 МБ
SHOWN_HEADERS = (
    "host",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
    "x-vercel-forwarded-for",
    "x-vercel-id",
    "user-agent",
)


@router.post("/cookie")
def set_cookie(response: Response) -> dict:
    value = secrets.token_hex(8)
    # Як майбутній refresh-cookie: httpOnly, без Domain (host-only), SameSite=Lax, лише для /api.
    response.set_cookie(
        COOKIE_NAME,
        value,
        max_age=600,
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="lax",
        path="/api",
    )
    return {"set": value}


@router.get("/cookie")
def read_cookie(request: Request) -> dict:
    value = request.cookies.get(COOKIE_NAME)
    return {"received": value is not None, "value": value}


@router.get("/request")
def request_info(request: Request) -> dict:
    return {
        "client_host": request.client.host if request.client else None,
        "scheme": request.url.scheme,
        "headers": {h: request.headers.get(h) for h in SHOWN_HEADERS},
    }


@router.post("/upload")
async def upload(file: UploadFile) -> dict:
    digest = hashlib.sha256()
    size = 0
    while chunk := await file.read(64 * 1024):
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="FILE_TOO_LARGE")
        digest.update(chunk)
    return {"filename": file.filename, "size": size, "sha256": digest.hexdigest()}
