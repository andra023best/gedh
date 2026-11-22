"""Безпечне приймання файлів (NFR-19).

Тип визначається за вмістом; файл зберігається в БД поза публічним каталогом.
"""

import hashlib
import uuid
from dataclasses import dataclass

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.models.technical import StoredFile

DOCUMENT_MAX = 2 * 1024 * 1024  # документ верифікації ≤ 2 МБ
# Сигнатури файлів (magic bytes) — заголовку Content-Type від клієнта не довіряємо
SIGNATURES = {
    b"%PDF-": "application/pdf",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
}


@dataclass
class Incoming:
    data: bytes
    content_type: str
    sha256: str


async def read_limited(upload: UploadFile, limit: int) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise ApiError(413, "FILE_TOO_LARGE", max_bytes=limit)
    return data


def sniff(data: bytes) -> str | None:
    return next((ctype for sig, ctype in SIGNATURES.items() if data.startswith(sig)), None)


async def read_document(upload: UploadFile) -> Incoming:
    data = await read_limited(upload, DOCUMENT_MAX)
    ctype = sniff(data)
    if ctype is None:
        raise ApiError(415, "UNSUPPORTED_FILE_TYPE", allowed=["PDF", "PNG", "JPEG"])
    return Incoming(data=data, content_type=ctype, sha256=hashlib.sha256(data).hexdigest())


def store(db: Session, incoming: Incoming, *, owner_id: uuid.UUID | None, purpose: str) -> uuid.UUID:
    f = StoredFile(
        owner_id=owner_id,
        purpose=purpose,
        content_type=incoming.content_type,
        size=len(incoming.data),
        sha256=incoming.sha256,
        data=incoming.data,
    )
    db.add(f)
    db.flush()
    return f.id
