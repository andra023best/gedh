"""Технічні таблиці, яких немає на діаграмах класів (нотатки для розробки, розд. 2; S-17)."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, tz, uuid_pk


class ActionToken(Base):
    """Одноразовий токен з листа: підтвердження e-mail, уточнення заявки, скидання пароля.

    Зберігається лише SHA-256 токена; сам токен є тільки в листі.
    """

    __tablename__ = "action_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # VERIFY_EMAIL | REGISTRATION_CLARIFY | RESET_PASSWORD
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    payload: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    expires_at: Mapped[datetime] = tz(nullable=False)
    used_at: Mapped[datetime | None] = tz()


class StoredFile(Base):
    """Файл у БД (bytea) поза публічним каталогом (NFR-19) зі строком видалення (табл. 3.26)."""

    __tablename__ = "stored_files"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    purpose: Mapped[str] = mapped_column(String(30))  # VERIFICATION_DOCUMENT | DATASET_UPLOAD
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = created_at()
    delete_after: Mapped[datetime | None] = tz()
