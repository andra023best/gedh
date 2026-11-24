"""Власний профіль і права суб'єкта персональних даних (UC-21; FR-34, FR-42; DG-10, DG-12, DG-24)."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api import serializers
from app.api.auth import CSRF_COOKIE, REFRESH_COOKIE
from app.core import audit, consent, security
from app.core.deps import current_user, get_db, request_id
from app.core.errors import ApiError
from app.core.proxy_guard import client_ip
from app.models.audit import AuditEvent
from app.models.datasets import RestrictedDataset
from app.models.enums import AccountStatus, AuditAction, ConsentAction, GrantStatus, Role
from app.models.exchange import AccessGrant, AccessRequest, DataExport
from app.models.identity import ConsentRecord, RegistrationRequest, User
from app.models.identity import Session as UserSession
from app.models.technical import Notification

router = APIRouter(prefix="/me", tags=["me"])


@router.get("")
def get_me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return serializers.me(db, user)


class MePatch(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    position: str | None = Field(default=None, max_length=200)


@router.patch("")
def update_me(
    body: MePatch, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(422, "NOTHING_TO_UPDATE")
    if "full_name" in changes and not changes["full_name"]:
        raise ApiError(422, "FULL_NAME_REQUIRED")
    for field, value in changes.items():
        setattr(user, field, value)
    # у журнал — лише назви змінених полів, без самих персональних даних (табл. 3.26, прим. 1)
    audit.append(
        db,
        action=AuditAction.CONFIG_CHANGE,
        object_type="user",
        object_id=user.id,
        request_id=request_id(request),
        actor_id=user.id,
        actor_role=user.role,
        details={"fields": sorted(changes)},
    )
    db.commit()
    return serializers.me(db, user)


class ConsentBody(BaseModel):
    text_version: str


@router.post("/consent", status_code=201)
def accept_consent(
    body: ConsentBody, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    if body.text_version != consent.CURRENT_VERSION:
        raise ApiError(400, "CONSENT_VERSION_OUTDATED", current_version=consent.CURRENT_VERSION)
    db.add(
        ConsentRecord(
            user_id=user.id,
            text_version=body.text_version,
            action=ConsentAction.GIVEN,
            ip_address=client_ip(request),
        )
    )
    audit.append(
        db,
        action=AuditAction.CONSENT_CHANGE,
        object_type="user",
        object_id=user.id,
        request_id=request_id(request),
        actor_id=user.id,
        actor_role=user.role,
        details={"consent": "GIVEN", "text_version": body.text_version},
    )
    db.commit()
    return {"message": "Згоду прийнято."}


class DeleteBody(BaseModel):
    password: str
    reason: Literal["DELETE_ACCOUNT", "WITHDRAW_CONSENT"]
    confirm_data_deletion: bool


@router.post("/delete", status_code=202)
def delete_me(
    body: DeleteBody, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    if not security.verify_password(user.password_hash, body.password):
        raise ApiError(403, "PASSWORD_INCORRECT")  # UC-21 EX1
    if not body.confirm_data_deletion:
        raise ApiError(422, "DELETION_CONFIRMATION_REQUIRED")  # DG-12
    now = datetime.now(UTC)
    if user.role == Role.DATA_PROVIDER and user.organization_id:  # UC-21 EX2
        other_providers = db.scalar(
            select(User.id).where(
                User.organization_id == user.organization_id,
                User.id != user.id,
                User.role == Role.DATA_PROVIDER,
                User.status == AccountStatus.ACTIVE,
            )
        )
        active_on_own_data = db.scalar(
            select(AccessGrant.id)
            .join(RestrictedDataset, RestrictedDataset.id == AccessGrant.dataset_id)
            .where(
                RestrictedDataset.owner_org_id == user.organization_id,
                AccessGrant.status == GrantStatus.ACTIVE,
            )
        )
        if active_on_own_data and not other_providers:
            raise ApiError(409, "PROVIDER_HAS_ACTIVE_GRANTS")

    rid = request_id(request)
    # Одна транзакція (UC-21 AC2): чинні дозволи завершуються з датою підтвердження видалення,
    # обліковий запис деактивується, сесії відкликаються; анонімізація — через 30 днів (DG-24)
    ended = db.execute(
        update(AccessGrant)
        .where(AccessGrant.grantee_id == user.id, AccessGrant.status != GrantStatus.EXPIRED)
        .values(status=GrantStatus.EXPIRED, deletion_confirmed_at=now)
        .returning(AccessGrant.id)
    ).all()
    db.execute(
        update(AccessGrant)
        .where(AccessGrant.grantee_id == user.id, AccessGrant.deletion_confirmed_at.is_(None))
        .values(deletion_confirmed_at=now)
    )
    db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    user.status = AccountStatus.DEACTIVATED
    user.deactivated_at = now
    if body.reason == "WITHDRAW_CONSENT":
        db.add(
            ConsentRecord(
                user_id=user.id,
                text_version=consent.CURRENT_VERSION,
                action=ConsentAction.WITHDRAWN,
                ip_address=client_ip(request),
            )
        )
        audit.append(
            db,
            action=AuditAction.CONSENT_CHANGE,
            object_type="user",
            object_id=user.id,
            request_id=rid,
            actor_id=user.id,
            actor_role=user.role,
            details={"consent": "WITHDRAWN"},
        )
    audit.append(
        db,
        action=AuditAction.ACCOUNT_DELETE,
        object_type="user",
        object_id=user.id,
        request_id=rid,
        actor_id=user.id,
        actor_role=user.role,
        details={"reason": body.reason, "grants_ended": len(ended)},
    )
    db.commit()
    response = Response(
        content='{"message":"Обліковий запис деактивовано; ПД буде анонімізовано через 30 днів."}',
        status_code=202,
        media_type="application/json",
    )
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response


@router.get("/export")
def export_personal_data(
    request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    """UC-21 AC1, FR-41 (GDPR ст. 15, 20): персональні дані, заявки й історія входів користувача — JSON.

    Файл формується одразу й віддається для збереження, на сервері не зберігається (замість посилання
    на 24 год зі звіту — S-34). Значень закритих даних у файлі немає — лише метадані заявок і дозволів.
    """
    iso = serializers.iso
    rr = db.scalar(select(RegistrationRequest).where(RegistrationRequest.user_id == user.id))
    logins = db.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.actor_id == user.id,
            AuditEvent.action.in_([AuditAction.LOGIN, AuditAction.LOGIN_FAILED]),
        )
        .order_by(AuditEvent.seq)
    )
    data = {
        "format": "GEDH personal data export v1",
        "generated_at": iso(datetime.now(UTC)),
        "profile": serializers.me(db, user),
        "account": {"status": user.status.value, "created_at": iso(user.created_at)},
        "registration_request": {
            "submitted_at": iso(rr.submitted_at),
            "account_type": rr.account_type.value,
            "purpose": rr.purpose,
            "status": rr.status.value,
            "decided_at": iso(rr.decided_at),
            "decision_reason": rr.decision_reason,
        }
        if rr
        else None,
        "consents": [
            {
                "text_version": c.text_version,
                "action": c.action.value,
                "recorded_at": iso(c.recorded_at),
                "ip": c.ip_address,
            }
            for c in db.scalars(
                select(ConsentRecord)
                .where(ConsentRecord.user_id == user.id)
                .order_by(ConsentRecord.recorded_at)
            )
        ],
        "login_history": [
            {"at": iso(e.occurred_at), "result": e.result.value, "request_id": e.request_id} for e in logins
        ],
        "sessions": [
            {"issued_at": iso(s.issued_at), "expires_at": iso(s.expires_at), "revoked_at": iso(s.revoked_at)}
            for s in db.scalars(
                select(UserSession).where(UserSession.user_id == user.id).order_by(UserSession.issued_at)
            )
        ],
        "access_requests": [
            {
                "number": r.number,
                "status": r.status.value,
                "submitted_at": iso(r.submitted_at),
                "country": r.country_code,
                "category": r.category_code,
                "period": [iso(r.period_start), iso(r.period_end)],
                "purpose": r.purpose,
                "usage_place": r.usage_place,
                "responsible_person": r.responsible_person,
                "requested_term_days": r.requested_term_days,
            }
            for r in db.scalars(
                select(AccessRequest)
                .where(AccessRequest.requester_id == user.id)
                .order_by(AccessRequest.submitted_at)
            )
        ],
        "grants": [
            {
                "id": str(g.id),
                "dataset_id": str(g.dataset_id),
                "period": [iso(g.period_start), iso(g.period_end)],
                "valid_from": iso(g.valid_from),
                "valid_to": iso(g.valid_to),
                "status": g.status.value,
                "deletion_confirmed_at": iso(g.deletion_confirmed_at),
            }
            for g in db.scalars(select(AccessGrant).where(AccessGrant.grantee_id == user.id))
        ],
        "data_exports": [
            {
                "at": iso(x.created_at),
                "format": x.format.value,
                "records": x.records_count,
                "file_sha256": x.file_hash,
            }
            for x in db.scalars(
                select(DataExport).where(DataExport.exported_by_id == user.id).order_by(DataExport.created_at)
            )
        ],
        "notifications": [
            {"at": iso(n.created_at), "title": n.title, "text": n.text, "read_at": iso(n.read_at)}
            for n in db.scalars(
                select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at)
            )
        ],
        "retention": "Строки зберігання — у тексті згоди (табл. 3.26 звіту); після видалення облікового "
        "запису "
        "дані анонімізуються через 30 днів.",
    }
    body = json.dumps(data, ensure_ascii=False, indent=2).encode()
    sha = hashlib.sha256(body).hexdigest()
    audit.append(
        db,
        action=AuditAction.PERSONAL_DATA_EXPORT,
        object_type="user",
        object_id=user.id,
        request_id=request_id(request),
        actor_id=user.id,
        actor_role=user.role,
        details={"file_sha256": sha, "sections": sorted(data)},
    )
    db.commit()
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="gedh-my-data-{datetime.now(UTC):%Y%m%d}.json"',
            "X-Content-SHA256": sha,
        },
    )
