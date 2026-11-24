"""Перегляд журналу аудиту DG-офіцером (UC-15; FR-31, FR-32, FR-40; DG-07, DG-17 – DG-19).

- Лише DG-офіцер. Будь-яка інша роль, зокрема адміністратор, отримує 403, а спробу записано як
  AUDIT_VIEW / DENIED (UC-15 EX1, D-38).
- Кожен перегляд, перевірка цілісності й експорт також записуються
  (AUDIT_VIEW, AUDIT_EXPORT; постумова UC-15).
- Сам запис журналу не містить ПІБ, e-mail чи IP (D-40). Для зручності DG-офіцера відповідь додає
  поточну назву виконавця й організації з довідників; після анонімізації облікового запису там
  буде «анонімізовано», а запис журналу не змінюється (DG-24).
"""

import csv
import hashlib
import io
import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import Select, String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.api.data import csv_safe
from app.core import audit
from app.core.deps import current_user, get_db, request_id
from app.core.errors import ApiError
from app.models.audit import GENESIS_HASH, AuditEvent
from app.models.datasets import Dataset, DatasetVersion, RestrictedDataset
from app.models.enums import AccountStatus, AuditAction, AuditResult, Role
from app.models.exchange import AccessGrant, AccessRequest, DataSharingAgreement
from app.models.identity import Organization, User
from app.models.ingestion import Connector, ImportRun

router = APIRouter(prefix="/audit", tags=["audit"])

EXPORT_MAX = 10_000


def dg_officer(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
    """UC-15 EX1: інші ролі — 403, спроба потрапляє в той самий журнал."""
    if user.role != Role.DG_OFFICER:
        request.state.denial_audited = True
        audit.append(
            db,
            action=AuditAction.AUDIT_VIEW,
            result=AuditResult.DENIED,
            object_type="audit_log",
            object_id=f"{request.method} {request.url.path}"[:64],
            request_id=request_id(request),
            actor_id=user.id,
            actor_role=user.role,
            on_behalf_of_id=user.organization_id,
            details={"reason": "FORBIDDEN_ROLE"},
        )
        db.commit()
        raise ApiError(403, "FORBIDDEN_ROLE")
    return user


class Filters:
    """Фільтри UC-15 крок 2: користувач, організація, країна, набір, тип дії, період (+ результат, об'єкт)."""

    def __init__(
        self,
        actor_id: uuid.UUID | None = None,
        actor_email: str | None = Query(None, max_length=254),
        organization_id: uuid.UUID | None = None,
        country: str | None = Query(None, pattern="^[A-Z]{3}$"),
        dataset_id: uuid.UUID | None = None,
        action: AuditAction | None = None,
        result: AuditResult | None = None,
        object_type: str | None = Query(None, max_length=40),
        object_id: str | None = Query(None, max_length=64),
        from_: datetime | None = Query(None, alias="from"),
        to: datetime | None = None,
    ) -> None:
        self.actor_id, self.actor_email, self.organization_id = actor_id, actor_email, organization_id
        self.country, self.dataset_id, self.action, self.result = country, dataset_id, action, result
        self.object_type, self.object_id, self.from_, self.to = object_type, object_id, from_, to

    def summary(self, actor_id: uuid.UUID | None) -> dict:
        """Що саме шукав DG-офіцер — у деталі AUDIT_VIEW (e-mail замінено на ідентифікатор, D-40)."""
        out = {
            "actor_id": str(actor_id) if actor_id else None,
            "organization_id": str(self.organization_id) if self.organization_id else None,
            "country": self.country,
            "dataset_id": str(self.dataset_id) if self.dataset_id else None,
            "action": self.action.value if self.action else None,
            "result": self.result.value if self.result else None,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "from": self.from_.isoformat() if self.from_ else None,
            "to": self.to.isoformat() if self.to else None,
        }
        return {k: v for k, v in out.items() if v is not None}


def _ids(q: Select) -> Select:
    return q.with_only_columns(cast(q.selected_columns[0], String))


def _dataset_objects(dataset_ids: Select) -> list:
    """Події набору: сам набір, версії, імпорти, дозволи (UC-15 AC2 — частково; повний lineage — FR-33)."""
    E = AuditEvent
    return [
        (E.object_type == "dataset") & E.object_id.in_(_ids(dataset_ids)),
        (E.object_type == "dataset_version")
        & E.object_id.in_(_ids(select(DatasetVersion.id).where(DatasetVersion.dataset_id.in_(dataset_ids)))),
        (E.object_type == "grant")
        & E.object_id.in_(_ids(select(AccessGrant.id).where(AccessGrant.dataset_id.in_(dataset_ids)))),
        (E.object_type == "import_run")
        & E.object_id.in_(
            _ids(
                select(ImportRun.id)
                .join(Connector, Connector.id == ImportRun.connector_id)
                .where(Connector.target_dataset_id.in_(dataset_ids))
            )
        ),
    ]


def build_query(db: Session, f: Filters) -> tuple[Select, uuid.UUID | None, bool]:
    """Повертає запит, ідентифікатор знайденого за e-mail користувача і ознаку «напевно порожньо»."""
    E = AuditEvent
    q = select(E)
    actor_id = f.actor_id
    if f.actor_email:
        found = db.scalar(select(User.id).where(func.lower(User.email) == f.actor_email.strip().lower()))
        if found is None:
            return q, None, True
        actor_id = found
    if actor_id:
        q = q.where(E.actor_id == actor_id)
    if f.organization_id:
        q = q.where(E.on_behalf_of_id == f.organization_id)
    if f.action:
        q = q.where(E.action == f.action)
    if f.result:
        q = q.where(E.result == f.result)
    if f.object_type:
        q = q.where(E.object_type == f.object_type)
    if f.object_id:
        q = q.where(E.object_id == f.object_id)
    if f.from_:
        q = q.where(E.occurred_at >= f.from_)
    if f.to:
        q = q.where(E.occurred_at <= f.to)
    if f.dataset_id:
        ds = select(Dataset.id).where(Dataset.id == f.dataset_id)
        req_ids = select(AccessRequest.id).where(AccessRequest.target_dataset_id == f.dataset_id)
        q = q.where(
            or_(
                *_dataset_objects(ds),
                (E.object_type == "access_request") & E.object_id.in_(_ids(req_ids)),
                (E.object_type == "agreement")
                & E.object_id.in_(
                    _ids(select(DataSharingAgreement.id).where(DataSharingAgreement.request_id.in_(req_ids)))
                ),
            )
        )
    if f.country:
        ds = select(RestrictedDataset.id).where(RestrictedDataset.country_code == f.country)
        req_ids = select(AccessRequest.id).where(AccessRequest.country_code == f.country)
        org_ids = select(Organization.id).where(Organization.country_code == f.country)
        q = q.where(
            or_(
                *_dataset_objects(ds),
                (E.object_type == "access_request") & E.object_id.in_(_ids(req_ids)),
                (E.object_type == "agreement")
                & E.object_id.in_(
                    _ids(select(DataSharingAgreement.id).where(DataSharingAgreement.request_id.in_(req_ids)))
                ),
                (E.object_type == "organization") & E.object_id.in_(_ids(org_ids)),
                (E.object_type == "country") & (E.object_id == f.country),  # профіль країни (UC-05)
                (E.object_type == "user")
                & E.object_id.in_(_ids(select(User.id).where(User.country_code == f.country))),
                E.on_behalf_of_id.in_(org_ids),
            )
        )
    return q, actor_id, False


def labels(db: Session, events: list[AuditEvent]) -> tuple[dict, dict]:
    users = {e.actor_id for e in events if e.actor_id}
    orgs = {e.on_behalf_of_id for e in events if e.on_behalf_of_id}
    u = {
        r.id: ("анонімізовано" if r.status == AccountStatus.ANONYMIZED else r.email)
        for r in (
            db.execute(select(User.id, User.email, User.status).where(User.id.in_(users))) if users else []
        )
    }
    o = (
        dict(db.execute(select(Organization.id, Organization.name).where(Organization.id.in_(orgs))).all())
        if orgs
        else {}
    )
    return u, o


def actor_none(e: AuditEvent) -> str:
    """Подія без виконавця: задача за розкладом або вхід з незнайомим e-mail (сам e-mail не пишемо)."""
    return "невідомий e-mail" if e.action == AuditAction.LOGIN_FAILED else "система"


def out(e: AuditEvent, users: dict, orgs: dict) -> dict:
    return {
        "seq": e.seq,
        "occurred_at": e.occurred_at,
        "actor_id": e.actor_id,
        "actor_role": e.actor_role,
        "actor_label": users.get(e.actor_id) if e.actor_id else actor_none(e),
        "on_behalf_of_id": e.on_behalf_of_id,
        "organization_label": orgs.get(e.on_behalf_of_id),
        "action": e.action,
        "object_type": e.object_type,
        "object_id": e.object_id,
        "result": e.result,
        "request_id": e.request_id,
        "details": e.details,
        "prev_hash": e.prev_hash,
        "hash": e.hash,
    }


def log_view(
    db: Session, request: Request, user: User, object_id: str, details: dict, action=AuditAction.AUDIT_VIEW
):
    audit.append(
        db,
        action=action,
        object_type="audit_log",
        object_id=object_id,
        request_id=request_id(request),
        actor_id=user.id,
        actor_role=user.role,
        on_behalf_of_id=user.organization_id,
        details=details,
    )
    db.commit()


@router.get("/events")
def list_events(
    request: Request,
    f: Filters = Depends(),
    before_seq: int | None = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(dg_officer),
    db: Session = Depends(get_db),
) -> dict:
    """UC-15 кроки 1–3 (FR-31): від нових до старих; наступна сторінка — `before_seq`."""
    q, actor_id, empty = build_query(db, f)
    items: list[AuditEvent] = []
    if not empty:
        if before_seq:
            q = q.where(AuditEvent.seq < before_seq)
        items = list(db.scalars(q.order_by(AuditEvent.seq.desc()).limit(limit + 1)))
    more = len(items) > limit
    items = items[:limit]
    users, orgs = labels(db, items)
    result = {
        "items": [out(e, users, orgs) for e in items],
        "next_before_seq": items[-1].seq if more else None,
    }
    log_view(db, request, user, "events", {"filters": f.summary(actor_id), "returned": len(items)})
    return result


@router.get("/events/{seq}")
def get_event(
    seq: int, request: Request, user: User = Depends(dg_officer), db: Session = Depends(get_db)
) -> dict:
    """UC-15 крок 4: запис з деталями й хешами; `hash_valid` — хеш відповідає вмісту й попередньому запису."""
    e = db.get(AuditEvent, seq)
    if e is None:
        raise ApiError(404, "NOT_FOUND")
    prev = db.get(AuditEvent, seq - 1) if seq > 1 else None
    expected_prev = prev.hash if prev else GENESIS_HASH
    users, orgs = labels(db, [e])
    result = out(e, users, orgs) | {
        "hash_valid": e.prev_hash == expected_prev
        and audit.compute_hash(audit.event_fields(e), e.prev_hash) == e.hash
    }
    log_view(db, request, user, str(seq), {"seq": seq})
    return result


@router.get("/integrity")
def integrity(request: Request, user: User = Depends(dg_officer), db: Session = Depends(get_db)) -> dict:
    """UC-15 AC1 (FR-32): перерахувати весь ланцюжок; «цілісність підтверджено» або перший зламаний запис."""
    started = datetime.now(UTC)
    broken = audit.verify_chain(db)
    checked = db.scalar(select(func.count()).select_from(AuditEvent)) or 0
    last = db.scalar(select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(1))
    result = {
        "ok": broken is None,
        "checked": checked,
        "first_broken_seq": broken,
        "last_seq": last.seq if last else 0,
        "last_hash": last.hash if last else GENESIS_HASH,
        "checked_at": started,
    }
    log_view(
        db, request, user, "integrity", {"ok": broken is None, "checked": checked, "first_broken_seq": broken}
    )
    return result


@router.get("/export")
def export_csv(
    request: Request,
    f: Filters = Depends(),
    user: User = Depends(dg_officer),
    db: Session = Depends(get_db),
) -> Response:
    """UC-15 крок 5 (FR-40): вибірка в CSV (до 10 000 записів), SHA-256 файлу — у заголовку й журналі."""
    q, actor_id, empty = build_query(db, f)
    items = [] if empty else list(db.scalars(q.order_by(AuditEvent.seq.desc()).limit(EXPORT_MAX)))
    users, orgs = labels(db, items)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["# GEDH — вибірка журналу аудиту (UC-15). Записи без ПІБ, e-mail та IP (D-40)."])
    w.writerow([f"# Фільтри: {json.dumps(f.summary(actor_id), ensure_ascii=False)}; записів: {len(items)}"])
    cols = ["seq", "occurred_at", "actor_id", "actor_role", "on_behalf_of_id", "organization", "action"]
    w.writerow([*cols, "object_type", "object_id", "result", "request_id", "details", "prev_hash", "hash"])
    for e in items:
        w.writerow(
            [
                e.seq,
                e.occurred_at.isoformat(),
                e.actor_id or "",
                e.actor_role.value if e.actor_role else "",
                e.on_behalf_of_id or "",
                csv_safe(orgs.get(e.on_behalf_of_id)),
                e.action.value,
                csv_safe(e.object_type),
                csv_safe(e.object_id),
                e.result.value,
                csv_safe(e.request_id),
                csv_safe(e.details),
                e.prev_hash,
                e.hash,
            ]
        )
    body = ("﻿" + buf.getvalue()).encode()
    sha = hashlib.sha256(body).hexdigest()
    log_view(
        db,
        request,
        user,
        "export",
        {"filters": f.summary(actor_id), "records": len(items), "file_sha256": sha},
        action=AuditAction.AUDIT_EXPORT,
    )
    name = f"gedh-audit-{datetime.now(UTC):%Y%m%d-%H%M}.csv"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Content-SHA256": sha},
    )
