"""Пакет ingestion — конектори, запуски імпорту, правила й звіти якості (рис. 5.5 звіту)."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, enum_col, tz, uuid_pk
from app.models.enums import QualityDimension, RunStatus, RunTrigger, Severity


class Connector(Base):
    __tablename__ = "connectors"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(100))
    source_system: Mapped[str] = mapped_column(String(40))
    base_url: Mapped[str] = mapped_column(String(500))
    schedule: Mapped[str] = mapped_column(String(100))  # cron
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # +target 1 (OpenDataset)
    target_dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id"))


class ImportRun(Base):
    __tablename__ = "import_runs"
    __table_args__ = (CheckConstraint("records_count >= 0", name="records_count_non_negative"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    connector_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connectors.id", ondelete="CASCADE"), index=True
    )
    trigger: Mapped[RunTrigger] = enum_col(RunTrigger)
    started_at: Mapped[datetime] = created_at()
    finished_at: Mapped[datetime | None] = tz()
    status: Mapped[RunStatus] = enum_col(RunStatus, default=RunStatus.RUNNING)
    request_url: Mapped[str] = mapped_column(String(1000))
    raw_payload_hash: Mapped[str | None] = mapped_column(String(64))
    records_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    # +produced 0..1
    produced_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_versions.id"), unique=True
    )


class QualityRule(Base):
    """Правило якості Q-01 – Q-11 (табл. 3.31 звіту)."""

    __tablename__ = "quality_rules"

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    dimension: Mapped[QualityDimension] = enum_col(QualityDimension)
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[Severity] = enum_col(Severity)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class QualityReport(Base):
    __tablename__ = "quality_reports"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 100", name="score_range"),
        CheckConstraint(
            "critical_count >= 0 AND warning_count >= 0 AND records_total >= 0", name="counts_non_negative"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # +version 0..1
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), unique=True
    )
    created_at: Mapped[datetime] = created_at()
    records_total: Mapped[int] = mapped_column(Integer)
    critical_count: Mapped[int] = mapped_column(Integer)
    warning_count: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    warnings_confirmed_at: Mapped[datetime | None] = tz()


class QualityIssue(Base):
    __tablename__ = "quality_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("quality_reports.id", ondelete="CASCADE"), index=True
    )
    # +violatedRule 1
    rule_code: Mapped[str] = mapped_column(ForeignKey("quality_rules.code"))
    record_ref: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
