"""Пакет datasets — набори даних, версії, спостереження, першоджерела (рис. 5.4 звіту).

Ієрархія Dataset {complete, disjoint} → OpenDataset | RestrictedDataset зберігається в одній таблиці
(single-table inheritance) зі стовпцем kind; обмеження CHECK гарантують, що кожен підклас має свої поля.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PeriodMixin, created_at, enum_col, period_check, tz, uuid_pk
from app.models.enums import Classification, DatasetStatus, VersionStatus


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (
        CheckConstraint("kind IN ('OPEN', 'RESTRICTED')", name="kind_valid"),
        # OpenDataset: джерело обов'язкове, полів закритого набору немає
        CheckConstraint(
            "kind <> 'OPEN' OR (source_system IS NOT NULL AND status IS NULL AND country_code IS NULL"
            " AND category_code IS NULL AND owner_org_id IS NULL AND classification = 'OPEN')",
            name="open_fields",
        ),
        # RestrictedDataset: статус, країна, категорія й власник обов'язкові (DG-02: клас RESTRICTED)
        CheckConstraint(
            "kind <> 'RESTRICTED' OR (status IS NOT NULL AND country_code IS NOT NULL"
            " AND category_code IS NOT NULL AND owner_org_id IS NOT NULL AND classification = 'RESTRICTED')",
            name="restricted_fields",
        ),
    )
    __mapper_args__ = {"polymorphic_on": "kind"}

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(10))
    title_ua: Mapped[str] = mapped_column(String(300))
    title_en: Mapped[str] = mapped_column(String(300))
    # {readOnly}: незмінність після створення гарантує тригер БД (DG-02, міграція 0001)
    classification: Mapped[Classification] = enum_col(Classification)
    licence: Mapped[str] = mapped_column(String(100))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created_at()


class OpenDataset(Dataset):
    __mapper_args__ = {"polymorphic_identity": "OPEN"}

    source_system: Mapped[str | None] = mapped_column(String(40), use_existing_column=True)


class RestrictedDataset(Dataset):
    __mapper_args__ = {"polymorphic_identity": "RESTRICTED"}

    status: Mapped[DatasetStatus | None] = enum_col(DatasetStatus, nullable=True, use_existing_column=True)
    country_code: Mapped[str | None] = mapped_column(ForeignKey("countries.code"), use_existing_column=True)
    category_code: Mapped[str | None] = mapped_column(
        ForeignKey("data_categories.code"), use_existing_column=True
    )
    # +owner 1
    owner_org_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), use_existing_column=True
    )


class DatasetVersion(Base):
    """Версія набору (вінтаж, D-05): старі значення не перезаписуються."""

    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version_no"),
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        CheckConstraint("status <> 'PUBLISHED' OR published_at IS NOT NULL", name="published_has_date"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[VersionStatus] = enum_col(VersionStatus, default=VersionStatus.QUARANTINE)
    file_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at()
    published_at: Mapped[datetime | None] = tz()
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    published_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    # +previous 0..1 / next 0..1 (lineage)
    previous_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("dataset_versions.id"), unique=True)


class SourceReference(Base):
    """Першоджерело (DG-06): композиція DatasetVersion ◆— SourceReference 0..1."""

    __tablename__ = "source_references"

    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), primary_key=True
    )
    document_title: Mapped[str] = mapped_column(String(300))
    document_number: Mapped[str | None] = mapped_column(String(100))
    document_date: Mapped[date | None] = mapped_column(Date)
    url: Mapped[str | None] = mapped_column(String(500))
    internal_ref: Mapped[str | None] = mapped_column(String(200))
    methodology: Mapped[str] = mapped_column(Text)
    responsible_person: Mapped[str] = mapped_column(String(200))


class Observation(PeriodMixin, Base):
    __tablename__ = "observations"
    __table_args__ = (
        period_check(),
        # {unique(country, indicator, breakdown, period) у межах версії}; NULL у breakdown — однакові (PG 15+)
        UniqueConstraint(
            "version_id",
            "country_code",
            "indicator_code",
            "breakdown",
            "period_start",
            "period_end",
            name="uq_observations_natural_key",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_observations_country_indicator", "country_code", "indicator_code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dataset_versions.id", ondelete="CASCADE"))
    country_code: Mapped[str] = mapped_column(ForeignKey("countries.code"))
    indicator_code: Mapped[str] = mapped_column(ForeignKey("indicators.code"))
    breakdown: Mapped[str | None] = mapped_column(String(100))
    value: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(String(300))
