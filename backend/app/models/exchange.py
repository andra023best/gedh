"""Пакет exchange — заявки, погодження, угоди, дозволи, вивантаження (рис. 5.6, 5.7 звіту)."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PeriodMixin, created_at, enum_col, period_check, tz, uuid_pk
from app.models.enums import (
    AccessRequestStatus,
    ApprovalStage,
    DecisionType,
    ExportChannel,
    ExportFormat,
    GrantStatus,
)


class AccessRequest(PeriodMixin, Base):
    """Заявка L2 на країну × категорію × період (D-03)."""

    __tablename__ = "access_requests"
    __table_args__ = (
        period_check(),
        CheckConstraint("requested_term_days BETWEEN 1 AND 180", name="term_max_180"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    number: Mapped[str] = mapped_column(String(20), unique=True)
    requester_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    on_behalf_of_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    country_code: Mapped[str] = mapped_column(ForeignKey("countries.code"))
    category_code: Mapped[str] = mapped_column(ForeignKey("data_categories.code"))
    # +target 0..1 — набір ще може не існувати (AWAITING_DATA)
    target_dataset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("datasets.id"))
    purpose: Mapped[str] = mapped_column(Text)
    usage_place: Mapped[str] = mapped_column(String(300))
    requested_term_days: Mapped[int] = mapped_column(Integer)
    responsible_person: Mapped[str] = mapped_column(String(200))
    status: Mapped[AccessRequestStatus] = enum_col(AccessRequestStatus, default=AccessRequestStatus.SUBMITTED)
    submitted_at: Mapped[datetime] = created_at()
    # Доповнення до рис. 5.6 (S-28): запит адміністратора на уточнення й відповідь заявника (UC-09 AC1)
    clarification_note: Mapped[str | None] = mapped_column(Text)
    clarification_answer: Mapped[str | None] = mapped_column(Text)


class ApprovalDecision(Base):
    """Подвійне погодження: адміністратор і постачальник, по одному рішенню на етап (0..2)."""

    __tablename__ = "approval_decisions"
    __table_args__ = (
        UniqueConstraint("request_id", "stage"),
        CheckConstraint(
            "approved_term_days IS NULL OR approved_term_days BETWEEN 1 AND 180", name="term_max_180"
        ),
        CheckConstraint(
            "approved_period_start IS NULL OR approved_period_start <= approved_period_end",
            name="approved_period_order",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("access_requests.id", ondelete="CASCADE"))
    stage: Mapped[ApprovalStage] = enum_col(ApprovalStage)
    decision: Mapped[DecisionType] = enum_col(DecisionType)
    reason: Mapped[str | None] = mapped_column(Text)
    approved_period_start: Mapped[date | None] = mapped_column(Date)
    approved_period_end: Mapped[date | None] = mapped_column(Date)
    approved_term_days: Mapped[int | None] = mapped_column(Integer)
    decided_at: Mapped[datetime] = created_at()
    # +decidedBy 1; {decidedBy ≠ requester} — тригер БД (D-01, DG-07, міграція 0001)
    decided_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    # S-42: постачальник може зафіксувати версію (вінтаж) замість найновішої — дані із запізненням
    approved_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("dataset_versions.id"))


class DataSharingAgreement(Base):
    """УОД (DG-11): умови фіксуються версією й хешем тексту."""

    __tablename__ = "data_sharing_agreements"

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("access_requests.id", ondelete="CASCADE"), unique=True
    )
    text_version: Mapped[str] = mapped_column(String(20))
    text_hash: Mapped[str] = mapped_column(String(64))
    # Доповнення (S-28): повний текст угоди з погодженим обсягом — доказ, з чим саме погодився заявник
    text: Mapped[str] = mapped_column(Text)
    offered_at: Mapped[datetime] = created_at()
    accepted_at: Mapped[datetime | None] = tz()


class AccessGrant(PeriodMixin, Base):
    """Дозвіл (D-03): країна × категорія × період, строк ≤ 180 днів."""

    __tablename__ = "access_grants"
    __table_args__ = (
        period_check(),
        CheckConstraint("valid_to > valid_from", name="validity_order"),
        CheckConstraint("valid_to - valid_from <= interval '180 days'", name="validity_max_180"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    agreement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_sharing_agreements.id"), unique=True)
    grantee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id"), index=True)
    valid_from: Mapped[datetime] = tz(nullable=False)
    valid_to: Mapped[datetime] = tz(nullable=False)
    status: Mapped[GrantStatus] = enum_col(GrantStatus, default=GrantStatus.ACTIVE)
    suspension_reason: Mapped[str | None] = mapped_column(Text)
    deletion_confirmed_at: Mapped[datetime | None] = tz()
    # S-42: зафіксована версія; порожньо — найновіша опублікована на момент звернення
    version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("dataset_versions.id"))


# DataExport → DatasetVersion: +sources 1..*
data_export_versions = Table(
    "data_export_versions",
    Base.metadata,
    Column("export_id", ForeignKey("data_exports.id", ondelete="CASCADE"), primary_key=True),
    Column("version_id", ForeignKey("dataset_versions.id"), primary_key=True),
)


class DataExport(Base):
    __tablename__ = "data_exports"
    __table_args__ = (CheckConstraint("records_count >= 0", name="records_count_non_negative"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    format: Mapped[ExportFormat] = enum_col(ExportFormat)
    channel: Mapped[ExportChannel] = enum_col(ExportChannel)
    records_count: Mapped[int] = mapped_column(Integer)
    file_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at()
    # +under 0..1 — для відкритих даних дозволу немає
    grant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("access_grants.id"), index=True)
    exported_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
