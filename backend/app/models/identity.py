"""РџР°РєРµС‚ identity вЂ” РѕР±Р»С–РєРѕРІС– Р·Р°РїРёСЃРё (СЂРёСЃ. 5.2 Р·РІС–С‚Сѓ)."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, enum_col, tz, uuid_pk
from app.models.enums import (
    AccountStatus,
    AccountType,
    ConsentAction,
    OrganizationType,
    ReviewStatus,
    Role,
    VerificationStatus,
)


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (CheckConstraint("country_code ~ '^[A-Z]{3}$'", name="country_code_iso3"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[OrganizationType] = enum_col(OrganizationType)
    country_code: Mapped[str] = mapped_column(String(3))  # ISO 3166-1 alpha-3; атрибут, не зв'язок (рис. 5.2)
    email_domain: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[VerificationStatus] = enum_col(VerificationStatus, default=VerificationStatus.PENDING)
    verified_at: Mapped[datetime | None] = tz()
    created_at: Mapped[datetime] = created_at()


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # e-mail СѓРЅС–РєР°Р»СЊРЅРёР№ Р±РµР· СѓСЂР°С…СѓРІР°РЅРЅСЏ СЂРµРіС–СЃС‚СЂСѓ
        Index("uq_users_email_lower", func.lower(text("email")), unique=True),
        CheckConstraint("failed_login_count >= 0", name="failed_login_count_non_negative"),
        CheckConstraint("country_code ~ '^[A-Z]{3}$'", name="country_code_iso3"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    full_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(254))
    password_hash: Mapped[str] = mapped_column(String(255))
    position: Mapped[str | None] = mapped_column(String(200))
    country_code: Mapped[str] = mapped_column(String(3))  # ISO 3166-1 alpha-3; атрибут, не зв'язок (рис. 5.2)
    role: Mapped[Role] = enum_col(Role, default=Role.ANALYST)
    status: Mapped[AccountStatus] = enum_col(AccountStatus, default=AccountStatus.PENDING_EMAIL)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = tz()
    deactivated_at: Mapped[datetime | None] = tz()
    created_at: Mapped[datetime] = created_at()
    # User в†’ Organization 0..1
    organization_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id"))


class RegistrationRequest(Base):
    """РљРѕРјРїРѕР·РёС†С–СЏ User в—†вЂ” RegistrationRequest 0..1."""

    __tablename__ = "registration_requests"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    account_type: Mapped[AccountType] = enum_col(AccountType)
    purpose: Mapped[str] = mapped_column(Text)
    verification_document_ref: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[ReviewStatus] = enum_col(ReviewStatus, default=ReviewStatus.PENDING)
    submitted_at: Mapped[datetime] = created_at()
    decided_at: Mapped[datetime | None] = tz()
    decision_reason: Mapped[str | None] = mapped_column(Text)
    # +reviewer 0..1
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    # Доповнення до рис. 5.2 (S-17): яку організацію вказав заявник і листування щодо уточнення (UC-02 AC1)
    requested_organization_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id"))
    requested_organization_name: Mapped[str | None] = mapped_column(String(200))
    clarification_note: Mapped[str | None] = mapped_column(Text)
    clarification_answer: Mapped[str | None] = mapped_column(Text)


class ConsentRecord(Base):
    """Р—Р°РїРёСЃ Р·РіРѕРґРё (DG-10): Р»РёС€Рµ РґРѕРґР°С”С‚СЊСЃСЏ, РЅРµ Р·РјС–РЅСЋС”С‚СЊСЃСЏ."""

    __tablename__ = "consent_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    text_version: Mapped[str] = mapped_column(String(20))
    action: Mapped[ConsentAction] = enum_col(ConsentAction)
    recorded_at: Mapped[datetime] = created_at()
    ip_address: Mapped[str | None] = mapped_column(String(45))


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    issued_at: Mapped[datetime] = created_at()
    expires_at: Mapped[datetime] = tz(nullable=False)
    revoked_at: Mapped[datetime | None] = tz()


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at()
    last_used_at: Mapped[datetime | None] = tz()
    revoked_at: Mapped[datetime | None] = tz()
