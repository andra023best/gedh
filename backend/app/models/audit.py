"""Пакет audit — журнал аудиту з ланцюжком хешів (рис. 5.3 звіту, DG-17 – DG-19, D-40).

AuditTrail — один рядок (id = 1) з останнім хешем і номером: append() блокує його
(SELECT … FOR UPDATE) безпосередньо перед COMMIT, тому ланцюжок не розгалужується.
Заборона UPDATE/DELETE для ролі застосунку й річні партиції — на етапі Е5 (модуль аудиту).
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, enum_col
from app.models.enums import AuditAction, AuditResult, Role

GENESIS_HASH = "0" * 64


class AuditTrail(Base):
    __tablename__ = "audit_trail"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    last_hash: Mapped[str] = mapped_column(String(64), default=GENESIS_HASH)
    last_seq: Mapped[int] = mapped_column(BigInteger, default=0)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    occurred_at: Mapped[datetime] = created_at()
    # +actor 0..1, +onBehalfOf 0..1
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    actor_role: Mapped[Role | None] = enum_col(Role, nullable=True)
    on_behalf_of_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id"))
    action: Mapped[AuditAction] = enum_col(AuditAction, index=True)
    object_type: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[str] = mapped_column(String(64))
    result: Mapped[AuditResult] = enum_col(AuditResult)
    request_id: Mapped[str] = mapped_column(String(64))
    details: Mapped[str] = mapped_column(Text, default="")
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)
