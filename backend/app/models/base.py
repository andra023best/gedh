"""Базовий клас моделей SQLAlchemy і спільні типи стовпців."""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, Date, DateTime, Enum, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import Uuid

# Однакові назви обмежень — щоб міграції Alembic були передбачувані.
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def enum_col(enum_cls: type[StrEnum], **kw) -> Mapped:
    """Перелік як VARCHAR + CHECK (не нативний тип PostgreSQL): нові значення додаються простою міграцією."""
    return mapped_column(
        Enum(enum_cls, native_enum=False, length=32, create_constraint=True, name=enum_cls.__name__.lower()),
        **kw,
    )


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def tz(**kw) -> Mapped:
    return mapped_column(DateTime(timezone=True), **kw)


class PeriodMixin:
    """Тип даних Period (рис. 5.8): start ≤ end."""

    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)


def period_check(prefix: str = "period") -> CheckConstraint:
    return CheckConstraint(f"{prefix}_start <= {prefix}_end", name=f"{prefix}_order")
