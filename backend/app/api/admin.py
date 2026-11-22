"""Розгляд заявок на реєстрацію й ведення організацій адміністратором (UC-02; FR-03, FR-04; D-01)."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import serializers
from app.api.auth import new_clarification_token
from app.core import audit, mail
from app.core.deps import get_db, request_id, require
from app.core.errors import ApiError
from app.models.enums import (
    AccountStatus,
    AuditAction,
    OrganizationType,
    ReviewStatus,
    Role,
    VerificationStatus,
)
from app.models.identity import Organization, RegistrationRequest, User
from app.models.technical import StoredFile

router = APIRouter(prefix="/admin", tags=["admin-registrations"])
admin_only = require(Role.ADMIN)
DOCUMENT_KEEP = timedelta(days=30)  # табл. 3.26: документ — до рішення + 30 днів
ISO3_RE = re.compile(r"^[A-Z]{3}$")


def get_rr(db: Session, rr_id: uuid.UUID) -> RegistrationRequest:
    rr = db.get(RegistrationRequest, rr_id)
    if rr is None:
        raise ApiError(404, "NOT_FOUND")
    return rr


def ensure_reviewable(rr: RegistrationRequest, admin: User) -> User:
    if rr.user_id == admin.id:
        raise ApiError(403, "SELF_REVIEW_FORBIDDEN")  # UC-02 EX2, D-01
    if rr.status not in (ReviewStatus.PENDING, ReviewStatus.NEEDS_CLARIFICATION):
        raise ApiError(409, "INVALID_STATE", status=rr.status.value)
    return rr


def decide(
    db: Session, rr: RegistrationRequest, admin: User, status: ReviewStatus, reason: str | None
) -> None:
    now = datetime.now(UTC)
    rr.status, rr.decided_at, rr.reviewer_id, rr.decision_reason = status, now, admin.id, reason
    if rr.verification_document_ref:
        doc = db.get(StoredFile, uuid.UUID(rr.verification_document_ref))
        if doc is not None:
            doc.delete_after = now + DOCUMENT_KEEP


@router.get("/registrations")
def list_registrations(
    status: ReviewStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> dict:
    q = select(RegistrationRequest).join(User, User.id == RegistrationRequest.user_id)
    if status is None:  # черга: лише заявки з підтвердженим e-mail, що чекають рішення (FR-02)
        q = q.where(
            User.status == AccountStatus.PENDING_REVIEW,
            RegistrationRequest.status.in_([ReviewStatus.PENDING, ReviewStatus.NEEDS_CLARIFICATION]),
        )
    else:
        q = q.where(RegistrationRequest.status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(RegistrationRequest.submitted_at).limit(limit).offset(offset)).all()
    return {"items": [serializers.registration(db, rr) for rr in rows], "total": total}


@router.get("/registrations/{rr_id}")
def get_registration(
    rr_id: uuid.UUID, admin: User = Depends(admin_only), db: Session = Depends(get_db)
) -> dict:
    return serializers.registration(db, get_rr(db, rr_id))


@router.get("/registrations/{rr_id}/document")
def get_registration_document(
    rr_id: uuid.UUID, admin: User = Depends(admin_only), db: Session = Depends(get_db)
) -> Response:
    rr = get_rr(db, rr_id)
    doc = (
        db.get(StoredFile, uuid.UUID(rr.verification_document_ref)) if rr.verification_document_ref else None
    )
    if doc is None:
        raise ApiError(404, "NOT_FOUND")
    ext = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}[doc.content_type]
    return Response(
        content=doc.data,
        media_type=doc.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="verification-{rr.id}.{ext}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


class ApproveBody(BaseModel):
    role: Literal["ANALYST", "ORG_REPRESENTATIVE", "DATA_PROVIDER"]
    organization_id: uuid.UUID | None = None


@router.post("/registrations/{rr_id}/approve")
def approve(
    rr_id: uuid.UUID,
    body: ApproveBody,
    request: Request,
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> dict:
    rr = ensure_reviewable(get_rr(db, rr_id), admin)
    user = db.get(User, rr.user_id)
    if user.status != AccountStatus.PENDING_REVIEW:
        raise ApiError(409, "INVALID_STATE", status=user.status.value)
    role = Role(body.role)
    org = None
    if role in (
        Role.ORG_REPRESENTATIVE,
        Role.DATA_PROVIDER,
    ):  # FR-04: належність до верифікованої організації
        if body.organization_id is None:
            raise ApiError(422, "ORGANIZATION_REQUIRED")
        org = db.get(Organization, body.organization_id)
        if org is None:
            raise ApiError(422, "ORGANIZATION_NOT_FOUND")
        if org.status != VerificationStatus.VERIFIED:
            raise ApiError(409, "ORGANIZATION_NOT_VERIFIED")
    user.role, user.status, user.organization_id = role, AccountStatus.ACTIVE, org.id if org else None
    decide(db, rr, admin, ReviewStatus.APPROVED, None)
    audit.append(
        db,
        action=AuditAction.REGISTRATION_DECISION,
        object_type="registration",
        object_id=rr.id,
        request_id=request_id(request),
        actor_id=admin.id,
        actor_role=admin.role,
        details={"decision": "APPROVED", "role": role.value, "organization_id": str(org.id) if org else None},
    )
    db.commit()
    mail.send(
        mail.Mail(
            to=user.email,
            subject="GEDH: обліковий запис схвалено",
            text=f"Вітаємо, {user.full_name}! Ваш обліковий запис на GEDH схвалено. "
            f"Увійти можна тут: {mail.page('/login')}",
        )
    )
    return serializers.registration(db, rr)


class ReasonBody(BaseModel):
    reason: str = Field(min_length=5, max_length=2000)


@router.post("/registrations/{rr_id}/reject")
def reject(
    rr_id: uuid.UUID,
    body: ReasonBody,
    request: Request,
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> dict:
    rr = ensure_reviewable(get_rr(db, rr_id), admin)
    user = db.get(User, rr.user_id)
    user.status = AccountStatus.REJECTED
    decide(db, rr, admin, ReviewStatus.REJECTED, body.reason)
    audit.append(
        db,
        action=AuditAction.REGISTRATION_DECISION,
        object_type="registration",
        object_id=rr.id,
        request_id=request_id(request),
        actor_id=admin.id,
        actor_role=admin.role,
        details={"decision": "REJECTED"},
    )
    db.commit()
    mail.send(
        mail.Mail(
            to=user.email,
            subject="GEDH: заявку на реєстрацію відхилено",
            text=f"На жаль, вашу заявку відхилено. Причина: {body.reason}",
        )
    )
    return serializers.registration(db, rr)


class ClarifyBody(BaseModel):
    note: str = Field(min_length=5, max_length=2000)


@router.post("/registrations/{rr_id}/clarify")
def clarify(
    rr_id: uuid.UUID,
    body: ClarifyBody,
    request: Request,
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> dict:
    rr = ensure_reviewable(get_rr(db, rr_id), admin)
    user = db.get(User, rr.user_id)
    rr.status, rr.clarification_note, rr.clarification_answer = (
        ReviewStatus.NEEDS_CLARIFICATION,
        body.note,
        None,
    )
    token = new_clarification_token(db, user)
    audit.append(
        db,
        action=AuditAction.REGISTRATION_DECISION,
        object_type="registration",
        object_id=rr.id,
        request_id=request_id(request),
        actor_id=admin.id,
        actor_role=admin.role,
        details={"decision": "NEEDS_CLARIFICATION"},
    )
    db.commit()
    mail.send(
        mail.Mail(
            to=user.email,
            subject="GEDH: потрібне уточнення заявки",
            text=f"Адміністратор просить уточнити заявку:\n\n{body.note}\n\n"
            f"Відповісти (посилання дійсне 7 днів, вхід не потрібен):\n"
            f"{mail.link('/registration-clarification', token)}",
        )
    )
    return serializers.registration(db, rr)


# ---------------------------------------------------------------------------- організації
@router.get("/organizations")
def list_organizations(admin: User = Depends(admin_only), db: Session = Depends(get_db)) -> list[dict]:
    return [serializers.org_full(o) for o in db.scalars(select(Organization).order_by(Organization.name))]


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    type: OrganizationType
    country_code: str
    email_domain: str | None = Field(default=None, max_length=100)


@router.post("/organizations", status_code=201)
def create_organization(
    body: OrganizationCreate,
    request: Request,
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> dict:
    if not ISO3_RE.match(body.country_code):
        raise ApiError(422, "INVALID_COUNTRY_CODE")
    domain = body.email_domain.strip().lower().lstrip("@") if body.email_domain else None
    org = Organization(
        name=body.name.strip(),
        type=body.type,
        country_code=body.country_code,
        email_domain=domain,
        status=VerificationStatus.VERIFIED,
        verified_at=datetime.now(UTC),
    )
    db.add(org)
    db.flush()
    audit.append(
        db,
        action=AuditAction.CONFIG_CHANGE,
        object_type="organization",
        object_id=org.id,
        request_id=request_id(request),
        actor_id=admin.id,
        actor_role=admin.role,
        details={"change": "CREATE_VERIFIED", "name": org.name, "country": org.country_code},
    )
    db.commit()
    return serializers.org_full(org)


@router.post("/organizations/{org_id}/verify")
def verify_organization(
    org_id: uuid.UUID, request: Request, admin: User = Depends(admin_only), db: Session = Depends(get_db)
) -> dict:
    org = db.get(Organization, org_id)
    if org is None:
        raise ApiError(404, "NOT_FOUND")
    if org.status == VerificationStatus.VERIFIED:
        raise ApiError(409, "INVALID_STATE", status=org.status.value)
    old = org.status.value
    org.status, org.verified_at = VerificationStatus.VERIFIED, datetime.now(UTC)
    audit.append(
        db,
        action=AuditAction.CONFIG_CHANGE,
        object_type="organization",
        object_id=org.id,
        request_id=request_id(request),
        actor_id=admin.id,
        actor_role=admin.role,
        details={"field": "status", "old": old, "new": "VERIFIED"},
    )
    db.commit()
    return serializers.org_full(org)
