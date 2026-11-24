"""Обмін закритими даними — ExchangeModule.

UC-08 – UC-11, UC-19; FR-18 – FR-20, FR-24, FR-25, FR-28, FR-44, FR-45.

Ланцюг (S-23): заявка представника → перше погодження адміністратора («хто й навіщо») → друге погодження
постачальника («що й скільки») → угода з погодженим обсягом → акцепт → дозвіл ≤ 180 днів.
Розподіл обов'язків (D-01, DG-07): ніхто не погоджує заявку своєї організації (і тригер БД — для заявника).
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.serializers import iso, org_public
from app.core import agreement as dsa_text
from app.core import audit, mail
from app.core.deps import current_user, get_db, request_id, require
from app.core.errors import ApiError
from app.models.datasets import DatasetVersion, RestrictedDataset
from app.models.enums import (
    AccessRequestStatus,
    AccountStatus,
    ApprovalStage,
    AuditAction,
    DatasetStatus,
    DecisionType,
    GrantStatus,
    Role,
    VerificationStatus,
    VersionStatus,
)
from app.models.exchange import AccessGrant, AccessRequest, ApprovalDecision, DataSharingAgreement
from app.models.identity import Organization, User
from app.models.reference import Country, DataCategory

router = APIRouter(tags=["access-requests"])
S = AccessRequestStatus
ACTIVE_STATUSES = (
    S.SUBMITTED,
    S.NEEDS_CLARIFICATION,
    S.PROVIDER_REVIEW,
    S.AWAITING_DATA,
    S.AWAITING_AGREEMENT,
)
OFFER_TTL = timedelta(days=14)  # UC-11 EX2


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------- допоміжне
def providers_of(db: Session, country: str) -> list[User]:
    """Активні постачальники організацій цієї країни."""
    return db.scalars(
        select(User)
        .join(Organization, Organization.id == User.organization_id)
        .where(
            User.role == Role.DATA_PROVIDER,
            User.status == AccountStatus.ACTIVE,
            Organization.country_code == country,
            Organization.status == VerificationStatus.VERIFIED,
        )
    ).all()


def published_target(db: Session, country: str, category: str) -> RestrictedDataset | None:
    """Чинний закритий набір для «країна × категорія» з опублікованою версією (найновіший)."""
    return db.scalars(
        select(RestrictedDataset)
        .join(DatasetVersion, DatasetVersion.dataset_id == RestrictedDataset.id)
        .where(
            RestrictedDataset.country_code == country,
            RestrictedDataset.category_code == category,
            RestrictedDataset.status == DatasetStatus.ACTIVE,
            DatasetVersion.status == VersionStatus.PUBLISHED,
        )
        .order_by(DatasetVersion.published_at.desc())
    ).first()


def notify(users, subject: str, text: str) -> None:
    for u in users:
        mail.send(mail.Mail(to=u.email, subject=subject, text=text))


def admins(db: Session) -> list[User]:
    return db.scalars(select(User).where(User.role == Role.ADMIN, User.status == AccountStatus.ACTIVE)).all()


def decision_of(db: Session, req: AccessRequest, stage: ApprovalStage) -> ApprovalDecision | None:
    return db.scalars(
        select(ApprovalDecision).where(ApprovalDecision.request_id == req.id, ApprovalDecision.stage == stage)
    ).first()


def approved_scope(db: Session, req: AccessRequest) -> tuple[date, date, int, bool]:
    """Погоджений постачальником обсяг: період, строк, чи звужено (UC-11 AC1)."""
    d = decision_of(db, req, ApprovalStage.PROVIDER)
    start = d.approved_period_start if d and d.approved_period_start else req.period_start
    end = d.approved_period_end if d and d.approved_period_end else req.period_end
    term = d.approved_term_days if d and d.approved_term_days else req.requested_term_days
    narrowed = bool(d and d.decision == DecisionType.APPROVED_NARROWED)
    return start, end, term, narrowed


def request_out(db: Session, req: AccessRequest) -> dict:
    org = db.get(Organization, req.on_behalf_of_id)
    requester = db.get(User, req.requester_id)
    decisions = []
    for d in db.scalars(
        select(ApprovalDecision)
        .where(ApprovalDecision.request_id == req.id)
        .order_by(ApprovalDecision.decided_at)
    ):
        by = db.get(User, d.decided_by_id)
        decisions.append(
            {
                "stage": d.stage.value,
                "decision": d.decision.value,
                "reason": d.reason,
                "approved_period": {"start": iso(d.approved_period_start), "end": iso(d.approved_period_end)}
                if d.approved_period_start
                else None,
                "approved_term_days": d.approved_term_days,
                "decided_at": iso(d.decided_at),
                "decided_by": f"{by.full_name} ({by.role.value})" if by else None,
            }
        )
    dsa = db.scalars(select(DataSharingAgreement).where(DataSharingAgreement.request_id == req.id)).first()
    grant = db.scalars(select(AccessGrant).where(AccessGrant.agreement_id == dsa.id)).first() if dsa else None
    target = db.get(RestrictedDataset, req.target_dataset_id) if req.target_dataset_id else None
    return {
        "id": str(req.id),
        "number": req.number,
        "status": req.status.value,
        "submitted_at": iso(req.submitted_at),
        "country_code": req.country_code,
        "category_code": req.category_code,
        "period": {"start": iso(req.period_start), "end": iso(req.period_end)},
        "purpose": req.purpose,
        "usage_place": req.usage_place,
        "requested_term_days": req.requested_term_days,
        "responsible_person": req.responsible_person,
        "requester": requester.full_name if requester else None,
        "requester_id": str(req.requester_id),
        "organization": org_public(org),
        "target_dataset_id": str(req.target_dataset_id) if req.target_dataset_id else None,
        "target_dataset_title": target.title_ua if target else None,
        "decisions": decisions,
        "clarification_note": req.clarification_note,
        "clarification_answer": req.clarification_answer,
        "agreement_id": str(dsa.id) if dsa else None,
        "offer_expires_at": iso(dsa.offered_at + OFFER_TTL) if dsa and not dsa.accepted_at else None,
        "grant_id": str(grant.id) if grant else None,
    }


def get_request(db: Session, req_id: uuid.UUID) -> AccessRequest:
    req = db.get(AccessRequest, req_id)
    if req is None:
        raise ApiError(404, "NOT_FOUND")
    return req


def can_see(db: Session, user: User, req: AccessRequest) -> bool:
    if user.role == Role.ADMIN:
        return True
    if user.role == Role.ORG_REPRESENTATIVE:
        return req.on_behalf_of_id == user.organization_id
    if (
        user.role == Role.DATA_PROVIDER
    ):  # лише заявки на країну своєї організації після першого погодження (UC-10 EX2)
        org = db.get(Organization, user.organization_id) if user.organization_id else None
        return bool(
            org
            and org.country_code == req.country_code
            and req.status not in (S.SUBMITTED, S.NEEDS_CLARIFICATION)
        )
    return False


def offer_agreement(db: Session, req: AccessRequest, target: RestrictedDataset) -> DataSharingAgreement:
    """Формує угоду з погодженим обсягом (UC-10 крок 5) і переводить заявку в AWAITING_AGREEMENT."""
    start, end, term, narrowed = approved_scope(db, req)
    owner = db.get(Organization, target.owner_org_id)
    requester_org = db.get(Organization, req.on_behalf_of_id)
    country = db.get(Country, req.country_code)
    category = db.get(DataCategory, req.category_code)
    text = dsa_text.render(
        number=req.number,
        requester_org=requester_org.name,
        responsible=req.responsible_person,
        owner_org=owner.name,
        dataset=target.title_ua,
        country=country.name_ua if country else req.country_code,
        category=category.name_ua if category else req.category_code,
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        term_days=term,
        purpose=req.purpose,
        usage_place=req.usage_place,
        narrowed=narrowed,
    )
    dsa = DataSharingAgreement(
        request_id=req.id, text_version=dsa_text.TEXT_VERSION, text=text, text_hash=dsa_text.text_hash(text)
    )
    db.add(dsa)
    req.target_dataset_id = target.id
    req.status = S.AWAITING_AGREEMENT
    db.flush()
    return dsa


def offer_waiting_requests(db: Session, dataset: RestrictedDataset) -> list[AccessRequest]:
    """Після публікації набору — угода для заявок, що чекали на дані (UC-10 AC1, UC-13 крок 5)."""
    waiting = db.scalars(
        select(AccessRequest).where(
            AccessRequest.status == S.AWAITING_DATA,
            AccessRequest.country_code == dataset.country_code,
            AccessRequest.category_code == dataset.category_code,
        )
    ).all()
    for req in waiting:
        offer_agreement(db, req, dataset)
    return waiting


# ---------------------------------------------------------------------------- заявка (UC-08)
class PeriodIn(BaseModel):
    start: date
    end: date


class RequestIn(BaseModel):
    country_code: str = Field(pattern="^[A-Z]{3}$")
    category_code: str
    period: PeriodIn
    purpose: str = Field(min_length=10, max_length=2000)
    usage_place: str = Field(min_length=2, max_length=300)
    requested_term_days: int = Field(ge=1, le=180)
    responsible_person: str = Field(min_length=2, max_length=200)


@router.post("/access-requests", status_code=201)
def create_request(
    body: RequestIn,
    request: Request,
    user: User = Depends(require(Role.ORG_REPRESENTATIVE)),
    db: Session = Depends(get_db),
) -> dict:
    org = db.get(Organization, user.organization_id) if user.organization_id else None
    if org is None or org.status != VerificationStatus.VERIFIED:
        raise ApiError(403, "ORGANIZATION_NOT_VERIFIED")  # UC-08 EX1
    if body.period.start > body.period.end:
        raise ApiError(422, "INVALID_PERIOD")
    if db.get(Country, body.country_code) is None or db.get(DataCategory, body.category_code) is None:
        raise ApiError(422, "UNKNOWN_COUNTRY_OR_CATEGORY")
    if not providers_of(db, body.country_code):
        raise ApiError(422, "NO_PROVIDER_FOR_COUNTRY")  # UC-08 EX3
    # DG-12 / FR-45: за завершеними дозволами організації не підтверджено видалення даних
    unconfirmed = db.scalar(
        select(AccessGrant.id)
        .join(User, User.id == AccessGrant.grantee_id)
        .where(
            User.organization_id == org.id,
            AccessGrant.deletion_confirmed_at.is_(None),
            or_(AccessGrant.status == GrantStatus.EXPIRED, AccessGrant.valid_to <= now()),
        )
    )
    if unconfirmed:
        raise ApiError(409, "DELETION_CONFIRMATION_REQUIRED", grant_id=str(unconfirmed))
    existing = db.scalar(
        select(AccessRequest.id).where(
            AccessRequest.on_behalf_of_id == org.id,
            AccessRequest.country_code == body.country_code,
            AccessRequest.category_code == body.category_code,
            AccessRequest.status.in_(ACTIVE_STATUSES),
        )
    )
    if existing:
        raise ApiError(409, "DUPLICATE_ACTIVE_REQUEST", existing_id=str(existing))  # UC-08 EX2
    year = now().year
    seq = (
        db.scalar(select(func.count(AccessRequest.id)).where(AccessRequest.number.like(f"GEDH-{year}-%")))
        or 0
    ) + 1
    target = published_target(db, body.country_code, body.category_code)  # може не бути (UC-08 AC1)
    req = AccessRequest(
        number=f"GEDH-{year}-{seq:04d}",
        requester_id=user.id,
        on_behalf_of_id=org.id,
        country_code=body.country_code,
        category_code=body.category_code,
        target_dataset_id=target.id if target else None,
        period_start=body.period.start,
        period_end=body.period.end,
        purpose=body.purpose,
        usage_place=body.usage_place,
        requested_term_days=body.requested_term_days,
        responsible_person=body.responsible_person,
        status=S.SUBMITTED,
    )
    db.add(req)
    db.flush()
    audit.append(
        db,
        action=AuditAction.REQUEST_SUBMIT,
        object_type="access_request",
        object_id=req.id,
        request_id=request_id(request),
        actor_id=user.id,
        actor_role=user.role,
        on_behalf_of_id=org.id,
        details={
            "number": req.number,
            "country": req.country_code,
            "category": req.category_code,
            "term_days": req.requested_term_days,
            "dataset_exists": target is not None,
        },
    )
    db.commit()
    notify(
        admins(db),
        "GEDH: нова заявка на закриті дані",
        f"Заявка {req.number} ({req.country_code} × {req.category_code}) очікує першого погодження.",
    )
    return request_out(db, req)


@router.get("/access-requests")
def list_requests(
    status: AccessRequestStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(require(Role.ORG_REPRESENTATIVE, Role.ADMIN, Role.DATA_PROVIDER)),
    db: Session = Depends(get_db),
) -> dict:
    q = select(AccessRequest).order_by(AccessRequest.submitted_at.desc())
    if status:
        q = q.where(AccessRequest.status == status)
    if user.role == Role.ORG_REPRESENTATIVE:
        q = q.where(AccessRequest.on_behalf_of_id == user.organization_id)
    elif user.role == Role.DATA_PROVIDER:
        org = db.get(Organization, user.organization_id) if user.organization_id else None
        q = q.where(
            AccessRequest.country_code == (org.country_code if org else "---"),
            AccessRequest.status.not_in([S.SUBMITTED, S.NEEDS_CLARIFICATION]),
        )
    rows = db.scalars(q).all()
    return {"items": [request_out(db, r) for r in rows[offset : offset + limit]], "total": len(rows)}


@router.get("/access-requests/{req_id}")
def get_access_request(
    req_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    req = get_request(db, req_id)
    if not can_see(db, user, req):
        raise ApiError(403, "FORBIDDEN_ROLE")
    return request_out(db, req)


# ---------------------------------------------------------------------------- перше погодження (UC-09)
class AdminDecisionIn(BaseModel):
    decision: Literal["APPROVED", "REJECTED", "NEEDS_CLARIFICATION"]
    reason: str | None = Field(default=None, max_length=2000)


@router.post("/access-requests/{req_id}/admin-decision")
def admin_decision(
    req_id: uuid.UUID,
    body: AdminDecisionIn,
    request: Request,
    user: User = Depends(require(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    req = get_request(db, req_id)
    if req.requester_id == user.id or (user.organization_id and user.organization_id == req.on_behalf_of_id):
        raise ApiError(403, "SELF_REVIEW_FORBIDDEN")  # D-01, DG-07, UC-09 EX3
    if req.status != S.SUBMITTED:
        raise ApiError(409, "INVALID_STATE", status=req.status.value)
    reason = (body.reason or "").strip()
    if body.decision != "APPROVED" and len(reason) < 5:
        raise ApiError(422, "REASON_REQUIRED")
    requester = db.get(User, req.requester_id)
    if (
        body.decision == "NEEDS_CLARIFICATION"
    ):  # не рішення етапу — заявка повернеться адміністратору (UC-09 AC1)
        req.status, req.clarification_note, req.clarification_answer = S.NEEDS_CLARIFICATION, reason, None
    else:
        db.add(
            ApprovalDecision(
                request_id=req.id,
                stage=ApprovalStage.ADMIN,
                decision=DecisionType(body.decision),
                reason=reason or None,
                decided_by_id=user.id,
            )
        )
        req.status = S.PROVIDER_REVIEW if body.decision == "APPROVED" else S.REJECTED
    audit.append(
        db,
        action=AuditAction.REQUEST_DECISION,
        object_type="access_request",
        object_id=req.id,
        request_id=request_id(request),
        actor_id=user.id,
        actor_role=user.role,
        details={"stage": "ADMIN", "decision": body.decision, "number": req.number},
    )
    db.commit()
    if req.status == S.PROVIDER_REVIEW:
        notify(
            providers_of(db, req.country_code),
            "GEDH: заявка на ваші дані",
            f"Заявка {req.number} ({req.category_code}) погоджена адміністратором "
            "і чекає вашого рішення щодо обсягу.",
        )
    else:
        verb = "відхилено" if req.status == S.REJECTED else "потребує уточнення"
        notify([requester], f"GEDH: заявка {req.number} — {verb}", f"Коментар адміністратора: {reason}")
    return request_out(db, req)


class ClarificationIn(BaseModel):
    answer: str = Field(min_length=5, max_length=2000)


@router.post("/access-requests/{req_id}/clarification")
def answer_clarification(
    req_id: uuid.UUID,
    body: ClarificationIn,
    request: Request,
    user: User = Depends(require(Role.ORG_REPRESENTATIVE)),
    db: Session = Depends(get_db),
) -> dict:
    req = get_request(db, req_id)
    if req.on_behalf_of_id != user.organization_id:
        raise ApiError(403, "FORBIDDEN_ROLE")
    if req.status != S.NEEDS_CLARIFICATION:
        raise ApiError(409, "INVALID_STATE", status=req.status.value)
    req.clarification_answer, req.status = body.answer, S.SUBMITTED
    audit.append(
        db,
        action=AuditAction.REQUEST_SUBMIT,
        object_type="access_request",
        object_id=req.id,
        request_id=request_id(request),
        actor_id=user.id,
        actor_role=user.role,
        on_behalf_of_id=user.organization_id,
        details={"step": "CLARIFICATION_ANSWERED"},
    )
    db.commit()
    notify(admins(db), "GEDH: відповідь на уточнення", f"Заявка {req.number} повернулася на розгляд.")
    return request_out(db, req)


# ---------------------------------------------------------------------------- друге погодження (UC-10)
class ProviderDecisionIn(BaseModel):
    decision: Literal["APPROVED", "APPROVED_NARROWED", "REJECTED"]
    approved_period: PeriodIn | None = None
    approved_term_days: int | None = Field(default=None, ge=1, le=180)
    reason: str | None = Field(default=None, max_length=2000)


@router.post("/access-requests/{req_id}/provider-decision")
def provider_decision(
    req_id: uuid.UUID,
    body: ProviderDecisionIn,
    request: Request,
    user: User = Depends(require(Role.DATA_PROVIDER)),
    db: Session = Depends(get_db),
) -> dict:
    req = get_request(db, req_id)
    org = db.get(Organization, user.organization_id) if user.organization_id else None
    if org is None or org.country_code != req.country_code:
        raise ApiError(403, "FORBIDDEN_ROLE")  # лише постачальник країни запиту (UC-10 EX2)
    target = db.get(RestrictedDataset, req.target_dataset_id) if req.target_dataset_id else None
    if target is not None and target.owner_org_id != org.id:
        raise ApiError(403, "FORBIDDEN_ROLE")  # обсяг погоджує власник набору
    if req.on_behalf_of_id == org.id:
        raise ApiError(403, "SELF_REVIEW_FORBIDDEN")  # D-01
    if req.status != S.PROVIDER_REVIEW:
        raise ApiError(409, "INVALID_STATE", status=req.status.value)
    reason = (body.reason or "").strip()
    start = end = term = None
    if body.decision == "REJECTED":
        if len(reason) < 5:
            raise ApiError(422, "REASON_REQUIRED")
    else:
        start = body.approved_period.start if body.approved_period else req.period_start
        end = body.approved_period.end if body.approved_period else req.period_end
        term = body.approved_term_days or req.requested_term_days
        # звузити можна, розширити — ні (UC-10 крок 3)
        if start > end or start < req.period_start or end > req.period_end or term > req.requested_term_days:
            raise ApiError(422, "SCOPE_NOT_NARROWER")
        narrowed = (start, end, term) != (req.period_start, req.period_end, req.requested_term_days)
        if body.decision == "APPROVED" and narrowed:
            body.decision = "APPROVED_NARROWED"
    db.add(
        ApprovalDecision(
            request_id=req.id,
            stage=ApprovalStage.PROVIDER,
            decision=DecisionType(body.decision),
            reason=reason or None,
            approved_period_start=start,
            approved_period_end=end,
            approved_term_days=term,
            decided_by_id=user.id,
        )
    )
    db.flush()
    if body.decision == "REJECTED":
        req.status = S.REJECTED
    else:
        target = target or published_target(db, req.country_code, req.category_code)
        if target is None or target.owner_org_id != org.id:
            req.status = S.AWAITING_DATA  # UC-10 AC1: набору ще немає — постачальник завантажує (UC-12)
        else:
            offer_agreement(db, req, target)
    audit.append(
        db,
        action=AuditAction.REQUEST_DECISION,
        object_type="access_request",
        object_id=req.id,
        request_id=request_id(request),
        actor_id=user.id,
        actor_role=user.role,
        on_behalf_of_id=org.id,
        details={
            "stage": "PROVIDER",
            "decision": body.decision,
            "number": req.number,
            "approved_period": [iso(start), iso(end)] if start else None,
            "term_days": term,
            "status": req.status.value,
        },
    )
    db.commit()
    requester = db.get(User, req.requester_id)
    texts = {
        S.REJECTED: f"Постачальник відхилив заявку. Причина: {reason}",
        S.AWAITING_DATA: "Постачальник погодив заявку; набір ще готується — "
        "угоду буде надіслано після публікації.",
        S.AWAITING_AGREEMENT: "Заявку погоджено. Перегляньте й прийміть угоду про обмін даними (14 днів).",
    }
    notify([requester], f"GEDH: заявка {req.number}", texts[req.status])
    return request_out(db, req)


@router.post("/access-requests/{req_id}/cancel")
def cancel_request(
    req_id: uuid.UUID,
    request: Request,
    user: User = Depends(require(Role.ORG_REPRESENTATIVE)),
    db: Session = Depends(get_db),
) -> dict:
    req = get_request(db, req_id)
    if req.requester_id != user.id:
        raise ApiError(403, "FORBIDDEN_ROLE")
    if req.status not in ACTIVE_STATUSES:
        raise ApiError(409, "INVALID_STATE", status=req.status.value)
    req.status = S.CANCELLED
    audit.append(
        db,
        action=AuditAction.REQUEST_DECISION,
        object_type="access_request",
        object_id=req.id,
        request_id=request_id(request),
        actor_id=user.id,
        actor_role=user.role,
        on_behalf_of_id=user.organization_id,
        details={"decision": "CANCELLED_BY_REQUESTER"},
    )
    db.commit()
    return request_out(db, req)


# ---------------------------------------------------------------------------- угода й дозвіл (UC-11)
def get_agreement(db: Session, req: AccessRequest) -> DataSharingAgreement:
    dsa = db.scalars(select(DataSharingAgreement).where(DataSharingAgreement.request_id == req.id)).first()
    if dsa is None:
        raise ApiError(404, "NOT_FOUND")
    return dsa


@router.get("/access-requests/{req_id}/agreement")
def agreement(req_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    req = get_request(db, req_id)
    if not can_see(db, user, req):
        raise ApiError(403, "FORBIDDEN_ROLE")
    dsa = get_agreement(db, req)
    start, end, term, narrowed = approved_scope(db, req)
    return {
        "id": str(dsa.id),
        "request_id": str(req.id),
        "text_version": dsa.text_version,
        "text": dsa.text,
        "text_hash": dsa.text_hash,
        "offered_at": iso(dsa.offered_at),
        "offer_expires_at": iso(dsa.offered_at + OFFER_TTL),
        "accepted_at": iso(dsa.accepted_at),
        "scope": {
            "country_code": req.country_code,
            "category_code": req.category_code,
            "period": {"start": iso(start), "end": iso(end)},
            "term_days": term,
            "dataset_id": str(req.target_dataset_id),
            "narrowed": narrowed,
        },
    }


def grant_out(db: Session, g: AccessGrant) -> dict:
    d = db.get(RestrictedDataset, g.dataset_id)
    dsa = db.get(DataSharingAgreement, g.agreement_id)
    req = db.get(AccessRequest, dsa.request_id) if dsa else None
    grantee = db.get(User, g.grantee_id)
    return {
        "id": str(g.id),
        "request_number": req.number if req else None,
        "request_id": str(req.id) if req else None,
        "dataset_id": str(g.dataset_id),
        "dataset_title": d.title_ua if d else None,
        "country_code": d.country_code if d else None,
        "category_code": d.category_code if d else None,
        "period": {"start": iso(g.period_start), "end": iso(g.period_end)},
        "valid_from": iso(g.valid_from),
        "valid_to": iso(g.valid_to),
        "status": g.status.value
        if not (g.status == GrantStatus.ACTIVE and g.valid_to <= now())
        else "EXPIRED",
        "suspension_reason": g.suspension_reason,
        "deletion_confirmed_at": iso(g.deletion_confirmed_at),
        "grantee": grantee.full_name if grantee else None,
        "is_synthetic": d.is_synthetic if d else None,
    }


class AcceptIn(BaseModel):
    text_hash: str = Field(pattern="^[0-9a-f]{64}$")


@router.post("/access-requests/{req_id}/agreement/accept", status_code=201)
def accept_agreement(
    req_id: uuid.UUID,
    body: AcceptIn,
    request: Request,
    user: User = Depends(require(Role.ORG_REPRESENTATIVE)),
    db: Session = Depends(get_db),
) -> dict:
    req = get_request(db, req_id)
    if req.requester_id != user.id:
        raise ApiError(403, "FORBIDDEN_ROLE")
    if req.status != S.AWAITING_AGREEMENT:
        raise ApiError(409, "INVALID_STATE", status=req.status.value)
    dsa = get_agreement(db, req)
    if body.text_hash != dsa.text_hash:
        raise ApiError(409, "AGREEMENT_TEXT_CHANGED")  # погоджуються саме з показаним текстом
    t = now()
    if dsa.offered_at + OFFER_TTL < t:
        req.status = S.CANCELLED
        db.commit()
        raise ApiError(409, "OFFER_EXPIRED")  # UC-11 EX2
    start, end, term, _ = approved_scope(db, req)
    dsa.accepted_at = t
    grant = AccessGrant(
        agreement_id=dsa.id,
        grantee_id=user.id,
        dataset_id=req.target_dataset_id,
        period_start=start,
        period_end=end,
        valid_from=t,
        valid_to=t + timedelta(days=term),
        status=GrantStatus.ACTIVE,
    )
    db.add(grant)
    req.status = S.GRANTED
    db.flush()
    rid = request_id(request)
    audit.append(
        db,
        action=AuditAction.AGREEMENT_ACCEPT,
        object_type="agreement",
        object_id=dsa.id,
        request_id=rid,
        actor_id=user.id,
        actor_role=user.role,
        on_behalf_of_id=user.organization_id,
        details={"number": req.number, "text_version": dsa.text_version, "text_sha256": dsa.text_hash},
    )
    audit.append(
        db,
        action=AuditAction.GRANT_ISSUE,
        object_type="grant",
        object_id=grant.id,
        request_id=rid,
        actor_id=user.id,
        actor_role=user.role,
        on_behalf_of_id=user.organization_id,
        details={
            "dataset_id": str(grant.dataset_id),
            "period": [iso(start), iso(end)],
            "valid_to": iso(grant.valid_to),
            "term_days": term,
        },
    )
    db.commit()
    d = db.get(RestrictedDataset, req.target_dataset_id)
    notify(
        [user],
        f"GEDH: дозвіл за заявкою {req.number}",
        f"Дозвіл на «{d.title_ua}» за період {start} … {end} діє до {grant.valid_to:%d.%m.%Y}.",
    )
    owners = db.scalars(
        select(User).where(User.organization_id == d.owner_org_id, User.role == Role.DATA_PROVIDER)
    )
    notify(
        list(owners) + admins(db),
        f"GEDH: видано дозвіл {req.number}",
        "Заявник прийняв угоду; дозвіл видано.",
    )
    return grant_out(db, grant)


@router.post("/access-requests/{req_id}/agreement/decline")
def decline_agreement(
    req_id: uuid.UUID,
    request: Request,
    user: User = Depends(require(Role.ORG_REPRESENTATIVE)),
    db: Session = Depends(get_db),
) -> dict:
    req = get_request(db, req_id)
    if req.requester_id != user.id:
        raise ApiError(403, "FORBIDDEN_ROLE")
    if req.status != S.AWAITING_AGREEMENT:
        raise ApiError(409, "INVALID_STATE", status=req.status.value)
    req.status = S.CANCELLED  # UC-11 EX1
    audit.append(
        db,
        action=AuditAction.REQUEST_DECISION,
        object_type="access_request",
        object_id=req.id,
        request_id=request_id(request),
        actor_id=user.id,
        actor_role=user.role,
        on_behalf_of_id=user.organization_id,
        details={"decision": "AGREEMENT_DECLINED"},
    )
    db.commit()
    return request_out(db, req)


# ---------------------------------------------------------------------------- дозволи
@router.get("/grants")
def list_grants(
    status: GrantStatus | None = None,
    user: User = Depends(require(Role.ORG_REPRESENTATIVE, Role.DATA_PROVIDER, Role.ADMIN, Role.DG_OFFICER)),
    db: Session = Depends(get_db),
) -> list[dict]:
    q = select(AccessGrant).order_by(AccessGrant.valid_from.desc())
    if user.role == Role.ORG_REPRESENTATIVE:
        q = q.where(AccessGrant.grantee_id == user.id)
    elif user.role == Role.DATA_PROVIDER:
        q = q.join(RestrictedDataset, RestrictedDataset.id == AccessGrant.dataset_id).where(
            RestrictedDataset.owner_org_id == user.organization_id
        )
    out = [grant_out(db, g) for g in db.scalars(q)]
    return [g for g in out if not status or g["status"] == status.value]


@router.get("/grants/{grant_id}")
def get_grant(grant_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    g = db.get(AccessGrant, grant_id)
    if g is None:
        raise ApiError(404, "NOT_FOUND")
    d = db.get(RestrictedDataset, g.dataset_id)
    allowed = (
        user.role in (Role.ADMIN, Role.DG_OFFICER)
        or g.grantee_id == user.id
        or (user.role == Role.DATA_PROVIDER and d and d.owner_org_id == user.organization_id)
    )
    if not allowed:
        raise ApiError(403, "FORBIDDEN_ROLE")
    return grant_out(db, g)


@router.post("/grants/{grant_id}/confirm-deletion")
def confirm_deletion(
    grant_id: uuid.UUID, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    """FR-44 / DG-12: отримувач підтверджує, що видалив дані за завершеним дозволом."""
    g = db.get(AccessGrant, grant_id)
    if g is None or g.grantee_id != user.id:
        raise ApiError(404, "NOT_FOUND")
    if g.status == GrantStatus.ACTIVE and g.valid_to > now():
        raise ApiError(409, "INVALID_STATE", status="ACTIVE")
    if g.deletion_confirmed_at is None:
        g.deletion_confirmed_at = now()
        audit.append(
            db,
            action=AuditAction.DELETION_CONFIRM,
            object_type="grant",
            object_id=g.id,
            request_id=request_id(request),
            actor_id=user.id,
            actor_role=user.role,
            on_behalf_of_id=user.organization_id,
        )
        db.commit()
    return grant_out(db, g)
