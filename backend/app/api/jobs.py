"""Службові задачі за розкладом (контракт: tag internal). Викликає GitHub Actions cron із X-Job-Token.

Запит проходить через Vercel, як і решта /api (S-08). Імпорт триває довше, ніж дозволяє проксі,
тому він запускається у фоні, а відповідь 202 повертається одразу. Завершення дозволів — швидке, синхронне.
"""

import hmac
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Request
from sqlalchemy import select

from app.api.exchange import OFFER_TTL, notify
from app.core import audit
from app.core.config import get_settings
from app.core.db import new_session
from app.core.deps import request_id
from app.core.errors import ApiError
from app.ingestion.service import run_all
from app.models.datasets import RestrictedDataset
from app.models.enums import AccessRequestStatus, AuditAction, GrantStatus, RunTrigger
from app.models.exchange import AccessGrant, AccessRequest, DataSharingAgreement
from app.models.identity import User

router = APIRouter(prefix="/internal/jobs", tags=["internal"])


def check_job_token(request: Request) -> None:
    expected = get_settings().job_token
    sent = request.headers.get("x-job-token", "")
    if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
        raise ApiError(401, "JOB_TOKEN_INVALID")


def _import_job(rid: str, only: set[str] | None) -> None:
    with new_session() as db:
        run_all(db, RunTrigger.SCHEDULE, request_id=rid, only=only)


@router.post("/import", status_code=202)
def import_open_data(request: Request, background: BackgroundTasks, indicator: str | None = None) -> dict:
    """UC-14: запуск усіх увімкнених конекторів (або одного показника)."""
    check_job_token(request)
    rid = request_id(request) or f"job-{uuid.uuid4()}"
    background.add_task(_import_job, rid, {indicator} if indicator else None)
    return {"message": "Імпорт запущено", "request_id": rid}


def expire_grants(rid: str, now: datetime | None = None) -> dict:
    """UC-19 (FR-28, DG-08): дозволи з минулим строком → EXPIRED.

    Заявнику — запит підтвердити видалення даних (DG-12).

    Додатково анулює пропозиції угод, не прийняті за 14 днів (UC-11 EX2). Ідемпотентно.
    """
    now = now or datetime.now(UTC)
    expired, cancelled = [], []
    with new_session() as db:
        for g in db.scalars(
            select(AccessGrant).where(AccessGrant.status == GrantStatus.ACTIVE, AccessGrant.valid_to <= now)
        ):
            g.status = GrantStatus.EXPIRED
            dsa = db.get(DataSharingAgreement, g.agreement_id)
            req = db.get(AccessRequest, dsa.request_id) if dsa else None
            if req is not None:
                req.status = AccessRequestStatus.EXPIRED
            audit.append(
                db,
                action=AuditAction.GRANT_EXPIRE,
                object_type="grant",
                object_id=g.id,
                request_id=rid,
                details={"valid_to": g.valid_to.isoformat(), "number": req.number if req else None},
            )
            expired.append(g)
        for dsa in db.scalars(
            select(DataSharingAgreement).where(
                DataSharingAgreement.accepted_at.is_(None), DataSharingAgreement.offered_at + OFFER_TTL <= now
            )
        ):
            req = db.get(AccessRequest, dsa.request_id)
            if req is not None and req.status == AccessRequestStatus.AWAITING_AGREEMENT:
                req.status = AccessRequestStatus.CANCELLED
                audit.append(
                    db,
                    action=AuditAction.REQUEST_DECISION,
                    object_type="access_request",
                    object_id=req.id,
                    request_id=rid,
                    details={"decision": "OFFER_EXPIRED", "number": req.number},
                )
                cancelled.append(req.number)
        db.commit()
        for g in expired:
            d = db.get(RestrictedDataset, g.dataset_id)
            notify(
                [db.get(User, g.grantee_id)],
                "GEDH: строк дозволу завершився",
                f"Дозвіл на «{d.title_ua}» завершився. За угодою видаліть отримані дані й підтвердьте "
                "видалення на сторінці «Мої дозволи» (DG-12).",
            )
    return {"expired": len(expired), "offers_cancelled": len(cancelled)}


@router.post("/expire-grants")
def job_expire_grants(request: Request) -> dict:
    check_job_token(request)
    return expire_grants(request_id(request) or f"job-{uuid.uuid4()}")
