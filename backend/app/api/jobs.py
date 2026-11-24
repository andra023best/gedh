"""Службові задачі за розкладом (контракт: tag internal). Викликає GitHub Actions cron із X-Job-Token.

Запит проходить через Vercel, як і решта /api (S-08). Імпорт триває довше, ніж дозволяє проксі,
тому він запускається у фоні, а відповідь 202 повертається одразу. Завершення дозволів — швидке, синхронне.
"""

import hmac
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Request
from sqlalchemy import delete, or_, select

from app.api.exchange import OFFER_TTL, notify
from app.core import audit
from app.core.config import get_settings
from app.core.db import new_session
from app.core.deps import request_id
from app.core.errors import ApiError
from app.ingestion.service import run_all
from app.models.datasets import RestrictedDataset
from app.models.enums import (
    AccessRequestStatus,
    AccountStatus,
    AuditAction,
    GrantStatus,
    ReviewStatus,
    RunTrigger,
)
from app.models.exchange import AccessGrant, AccessRequest, DataSharingAgreement
from app.models.identity import ApiKey, RegistrationRequest, User
from app.models.identity import Session as UserSession
from app.models.technical import ActionToken, RawPayload, StoredFile

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


ANONYMIZE_AFTER = timedelta(days=30)  # табл. 3.26, DG-24: обліковий запис — поки активний + 30 днів
UNCONFIRMED_KEEP = timedelta(days=30)  # реєстрація без підтвердження e-mail — так само 30 днів
TOKEN_KEEP = timedelta(days=30)  # технічні записи: використані або прострочені токени й сесії
ANON_NAME = "Анонімізований користувач"


def anonymize(db, user: User, now: datetime) -> None:
    """DG-24: ПІБ, e-mail і посада стираються; ідентифікатор лишається псевдонімом у журналі аудиту.

    Записи згоди (з IP) зберігаються ще 3 роки (табл. 3.26) — їх не змінює й тригер DG-10.
    """
    user.full_name = ANON_NAME
    user.email = f"anonymized-{user.id.hex}@anonymized.invalid"
    user.position = None
    user.password_hash = "!"  # noqa: S105 — не є хешем Argon2, тож увійти неможливо
    user.status = AccountStatus.ANONYMIZED
    user.locked_until = None
    user.deactivated_at = user.deactivated_at or now
    rr = db.scalar(select(RegistrationRequest).where(RegistrationRequest.user_id == user.id))
    if rr is not None:
        rr.purpose = "(анонімізовано)"
        rr.clarification_note = None
        rr.clarification_answer = None
        rr.decision_reason = None
    db.execute(delete(ActionToken).where(ActionToken.user_id == user.id))
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.execute(delete(ApiKey).where(ApiKey.user_id == user.id))


def apply_retention(rid: str, now: datetime | None = None) -> dict:
    """DG-23: строки зберігання з табл. 3.26 — щоденно; кожна категорія з кількістю записів — у журнал аудиту.

    1. Облікові записи: деактивовані понад 30 днів, відхилені заявки на реєстрацію понад 30 днів,
       непідтверджені e-mail понад 30 днів → анонімізація (DG-24).
    2. Файли (документи верифікації, відхилені файли карантину) з минулим `delete_after` → видалення.
    3. Сирі відповіді API старші 90 днів → видалення (хеш лишається в журналі запуску).
    4. Технічні записи: використані чи прострочені токени дій і сесії старші 30 днів.
    Журнал аудиту ця задача не чіпає (DG-19). Ідемпотентно.
    """
    now = now or datetime.now(UTC)
    edge = now - ANONYMIZE_AFTER
    counts: dict[str, int] = {}
    with new_session() as db:
        rejected = select(RegistrationRequest.user_id).where(
            RegistrationRequest.status == ReviewStatus.REJECTED, RegistrationRequest.decided_at <= edge
        )
        users = list(
            db.scalars(
                select(User).where(
                    User.status != AccountStatus.ANONYMIZED,
                    or_(
                        (User.status == AccountStatus.DEACTIVATED) & (User.deactivated_at <= edge),
                        (User.status == AccountStatus.REJECTED) & User.id.in_(rejected),
                        (User.status == AccountStatus.PENDING_EMAIL)
                        & (User.created_at <= now - UNCONFIRMED_KEEP),
                    ),
                )
            )
        )
        for u in users:
            anonymize(db, u, now)
        counts["accounts_anonymized"] = len(users)
        counts["files_deleted"] = db.execute(
            delete(StoredFile).where(StoredFile.delete_after.is_not(None), StoredFile.delete_after <= now)
        ).rowcount
        counts["raw_payloads_deleted"] = db.execute(
            delete(RawPayload).where(RawPayload.delete_after <= now)
        ).rowcount
        counts["tokens_deleted"] = db.execute(
            delete(ActionToken).where(
                or_(ActionToken.expires_at <= now - TOKEN_KEEP, ActionToken.used_at <= now - TOKEN_KEEP)
            )
        ).rowcount
        counts["sessions_deleted"] = db.execute(
            delete(UserSession).where(
                or_(UserSession.expires_at <= now - TOKEN_KEEP, UserSession.revoked_at <= now - TOKEN_KEEP)
            )
        ).rowcount
        for category, n in counts.items():
            if n:
                audit.append(
                    db,
                    action=AuditAction.RETENTION_PURGE,
                    object_type="retention",
                    object_id=category,
                    request_id=rid,
                    details={"category": category, "count": n},
                )
        for u in users:  # кожна анонімізація окремо: DG-офіцер бачить, чий псевдонім став знеособленим
            audit.append(
                db,
                action=AuditAction.ACCOUNT_DELETE,
                object_type="user",
                object_id=u.id,
                request_id=rid,
                details={"step": "ANONYMIZED"},
            )
        db.commit()
    return counts


@router.post("/retention")
def job_retention(request: Request) -> dict:
    """DG-23, DG-24: щоденно після завершення дозволів (.github/workflows/daily.yml)."""
    check_job_token(request)
    return apply_retention(request_id(request) or f"job-{uuid.uuid4()}")
