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


class RawPayload(Base):
    """Сира відповідь API джерела (UC-14 крок 3).

    Тіло стиснуто gzip; SHA-256 — від нестиснутих байтів; зберігається 90 днів (табл. 3.26).
    """

    __tablename__ = "raw_payloads"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_runs.id", ondelete="CASCADE"), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    body_gzip: Mapped[bytes] = mapped_column(LargeBinary)
    fetched_at: Mapped[datetime] = created_at()
    delete_after: Mapped[datetime] = tz(nullable=False)


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


class Notification(Base):
    """Сповіщення в інтерфейсі (FR-29); той самий текст іде листом (FR-39).

    Без значень закритих даних (DG-25). `kind` + `ref` — щоб не надсилати повторно те саме нагадування
    (UC-19 AC2: за 7 днів до завершення дозволу). Зберігається 180 днів (S-34).
    """

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    ref: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = created_at()
    read_at: Mapped[datetime | None] = tz()
