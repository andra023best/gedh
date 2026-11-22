"""Реєстрація, підтвердження e-mail, вхід, оновлення сесії (UC-01, UC-02 AC1, UC-03; FR-01, FR-02, FR-05)."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import files
from app.api.serializers import user_brief
from app.core import audit, consent, mail, ratelimit, security
from app.core.config import get_settings
from app.core.deps import get_db, request_id
from app.core.errors import ApiError
from app.core.proxy_guard import client_ip
from app.models.enums import (
    AccountStatus,
    AccountType,
    AuditAction,
    AuditResult,
    ConsentAction,
    ReviewStatus,
    Role,
    VerificationStatus,
)
from app.models.identity import ConsentRecord, Organization, RegistrationRequest, User
from app.models.identity import Session as UserSession
from app.models.technical import ActionToken

router = APIRouter(prefix="/auth", tags=["auth"])

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
ISO3_RE = re.compile(r"^[A-Z]{3}$")
VERIFY_TTL = timedelta(hours=24)
CLARIFY_TTL = timedelta(days=7)
REFRESH_COOKIE = "gedh_refresh"
CSRF_COOKIE = "gedh_csrf"
ACCEPTED = {"message": "Якщо дані коректні, на вказану адресу надіслано лист. Перевірте пошту."}


def now() -> datetime:
    return datetime.now(UTC)


def find_user_by_email(db: Session, email: str) -> User | None:
    return db.scalars(select(User).where(func.lower(User.email) == email.lower())).first()


def issue_token(db: Session, user: User, kind: str, ttl: timedelta) -> str:
    token = security.new_token()
    db.add(
        ActionToken(user_id=user.id, kind=kind, token_hash=security.token_hash(token), expires_at=now() + ttl)
    )
    return token


def take_token(db: Session, token: str, kind: str, *, consume: bool = True) -> ActionToken:
    row = db.scalars(
        select(ActionToken).where(
            ActionToken.token_hash == security.token_hash(token), ActionToken.kind == kind
        )
    ).first()
    if row is None or row.used_at is not None:
        raise ApiError(400, "TOKEN_INVALID")
    if row.expires_at <= now():
        raise ApiError(410, "TOKEN_EXPIRED")
    if consume:
        row.used_at = now()
    return row


def verification_mail(user: User, token: str) -> mail.Mail:
    return mail.Mail(
        to=user.email,
        subject="GEDH: підтвердіть e-mail",
        text=f"Вітаємо, {user.full_name}!\n\n"
        "Щоб завершити реєстрацію, перейдіть за посиланням (дійсне 24 год):\n"
        f"{mail.link('/verify-email', token)}\n\n"
        "Якщо ви не реєструвалися на GEDH, просто проігноруйте лист.",
    )


# ---------------------------------------------------------------------------- згода
@router.get("/consent")
def get_consent() -> dict:
    return {
        "text_version": consent.CURRENT_VERSION,
        "text": consent.current_text(),
        "published_at": consent.PUBLISHED_AT.isoformat(),
    }


# ---------------------------------------------------------------------------- реєстрація
@router.post("/register", status_code=202)
async def register(
    request: Request,
    full_name: Annotated[str, Form(min_length=2, max_length=200)],
    email: Annotated[str, Form(max_length=254)],
    password: Annotated[str, Form(max_length=128)],
    country_code: Annotated[str, Form()],
    account_type: Annotated[AccountType, Form()],
    purpose: Annotated[str, Form(min_length=10, max_length=2000)],
    consent_accepted: Annotated[bool, Form()],
    consent_text_version: Annotated[str, Form()],
    organization_id: Annotated[uuid.UUID | None, Form()] = None,
    organization_name: Annotated[str | None, Form(max_length=200)] = None,
    position: Annotated[str | None, Form(max_length=200)] = None,
    verification_document: Annotated[UploadFile | None, File()] = None,
    db: Session = Depends(get_db),
) -> dict:
    ratelimit.check(request, "register")
    if not consent_accepted:
        raise ApiError(400, "CONSENT_REQUIRED")  # UC-01 EX2
    if consent_text_version != consent.CURRENT_VERSION:
        raise ApiError(400, "CONSENT_VERSION_OUTDATED", current_version=consent.CURRENT_VERSION)
    email = email.strip()
    if not EMAIL_RE.match(email):
        raise ApiError(422, "INVALID_EMAIL")
    if not ISO3_RE.match(country_code):
        raise ApiError(422, "INVALID_COUNTRY_CODE")
    if problems := security.password_problems(password):
        raise ApiError(422, "WEAK_PASSWORD", problems=problems)

    organization = None
    document = await files.read_document(verification_document) if verification_document else None
    if account_type == AccountType.ORGANIZATION_MEMBER:
        if organization_id:
            organization = db.get(Organization, organization_id)
            if organization is None or organization.status != VerificationStatus.VERIFIED:
                raise ApiError(422, "ORGANIZATION_NOT_FOUND")
        elif not (organization_name and organization_name.strip()):
            raise ApiError(422, "ORGANIZATION_REQUIRED")
        domain_ok = bool(
            organization
            and organization.email_domain
            and email.lower().endswith("@" + organization.email_domain)
        )
        if not domain_ok and document is None:
            raise ApiError(422, "VERIFICATION_DOCUMENT_REQUIRED")  # UC-01 AC1

    existing = find_user_by_email(db, email)
    if existing is not None:  # UC-01 EX1: не розкриваємо, що адреса зайнята
        mail.send(
            mail.Mail(
                to=existing.email,
                subject="GEDH: спроба повторної реєстрації",
                text="Хтось спробував зареєструватися на GEDH з вашою адресою. Якщо це ви й забули пароль, "
                "скористайтеся відновленням пароля на сторінці входу. Якщо ні — просто проігноруйте лист.",
            )
        )
        return ACCEPTED

    user = User(
        full_name=full_name.strip(),
        email=email,
        password_hash=security.hash_password(password),
        position=position,
        country_code=country_code,
        role=Role.ANALYST,
        status=AccountStatus.PENDING_EMAIL,
    )
    db.add(user)
    db.flush()
    db.add(
        ConsentRecord(
            user_id=user.id,
            text_version=consent.CURRENT_VERSION,
            action=ConsentAction.GIVEN,
            ip_address=client_ip(request),
        )
    )
    doc_id = (
        files.store(db, document, owner_id=user.id, purpose="VERIFICATION_DOCUMENT") if document else None
    )
    db.add(
        RegistrationRequest(
            user_id=user.id,
            account_type=account_type,
            purpose=purpose,
            status=ReviewStatus.PENDING,
            verification_document_ref=str(doc_id) if doc_id else None,
            requested_organization_id=organization.id if organization else None,
            requested_organization_name=None if organization else (organization_name or None),
        )
    )
    token = issue_token(db, user, "VERIFY_EMAIL", VERIFY_TTL)
    audit.append(
        db,
        action=AuditAction.CONSENT_CHANGE,
        object_type="user",
        object_id=user.id,
        request_id=request_id(request),
        actor_id=user.id,
        details={"consent": "GIVEN", "text_version": consent.CURRENT_VERSION},
    )
    db.commit()
    mail.send(verification_mail(user, token))
    return ACCEPTED


class TokenBody(BaseModel):
    token: str


@router.post("/verify-email")
def verify_email(body: TokenBody, db: Session = Depends(get_db)) -> dict:
    row = take_token(db, body.token, "VERIFY_EMAIL")
    user = db.get(User, row.user_id)
    if user.status == AccountStatus.PENDING_EMAIL:
        user.status = AccountStatus.PENDING_REVIEW  # FR-02: лише тепер заявка йде адміністратору
    db.commit()
    return {"message": "E-mail підтверджено. Заявку передано адміністратору на розгляд."}


class EmailBody(BaseModel):
    email: str


@router.post("/resend-verification", status_code=202)
def resend_verification(body: EmailBody, request: Request, db: Session = Depends(get_db)) -> dict:
    ratelimit.check(request, "mail")
    user = find_user_by_email(db, body.email.strip())
    if user is not None and user.status == AccountStatus.PENDING_EMAIL:
        token = issue_token(db, user, "VERIFY_EMAIL", VERIFY_TTL)
        db.commit()
        mail.send(verification_mail(user, token))
    return ACCEPTED


# ---------------------------------------------------------------------------- уточнення заявки (UC-02 AC1)
@router.get("/registration-clarification/{token}")
def get_clarification(token: str, db: Session = Depends(get_db)) -> dict:
    row = take_token(db, token, "REGISTRATION_CLARIFY", consume=False)
    rr = db.scalars(select(RegistrationRequest).where(RegistrationRequest.user_id == row.user_id)).one()
    return {"note": rr.clarification_note or "", "expires_at": row.expires_at.isoformat()}


@router.post("/registration-clarification/{token}")
async def answer_clarification(
    token: str,
    answer: Annotated[str, Form(min_length=1, max_length=2000)],
    verification_document: Annotated[UploadFile | None, File()] = None,
    db: Session = Depends(get_db),
) -> dict:
    row = take_token(db, token, "REGISTRATION_CLARIFY")
    rr = db.scalars(select(RegistrationRequest).where(RegistrationRequest.user_id == row.user_id)).one()
    if rr.status != ReviewStatus.NEEDS_CLARIFICATION:
        raise ApiError(409, "INVALID_STATE", status=rr.status.value)
    if verification_document is not None:
        document = await files.read_document(verification_document)
        rr.verification_document_ref = str(
            files.store(db, document, owner_id=rr.user_id, purpose="VERIFICATION_DOCUMENT")
        )
    rr.clarification_answer = answer
    rr.status = ReviewStatus.PENDING
    db.commit()
    return {"message": "Відповідь передано адміністратору."}


# ---------------------------------------------------------------------------- вхід і сесії
class LoginBody(BaseModel):
    email: str
    password: str


def open_session(db: Session, user: User, response: Response) -> dict:
    refresh = security.new_token()
    s = UserSession(
        user_id=user.id,
        refresh_token_hash=security.token_hash(refresh),
        expires_at=now() + security.REFRESH_TTL,
    )
    db.add(s)
    db.flush()
    secure = get_settings().cookie_secure
    max_age = int(security.REFRESH_TTL.total_seconds())
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/api/auth",
    )
    response.set_cookie(
        CSRF_COOKIE,
        security.new_token(),
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )
    accepted = db.scalars(
        select(ConsentRecord.text_version)
        .where(ConsentRecord.user_id == user.id, ConsentRecord.action == ConsentAction.GIVEN)
        .order_by(ConsentRecord.recorded_at.desc())
    ).first()
    return {
        "access_token": security.create_access_token(user.id, user.role.value, s.id),
        "token_type": "bearer",
        "expires_in": int(security.ACCESS_TTL.total_seconds()),
        "user": user_brief(db, user),
        "consent_update_required": accepted != consent.CURRENT_VERSION,  # UC-03 AC2
    }


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    ratelimit.check(request, "login")
    rid = request_id(request)
    user = find_user_by_email(db, body.email.strip())
    t = now()
    if user is not None and user.locked_until and user.locked_until > t:
        raise ApiError(423, "ACCOUNT_LOCKED", retry_after=int((user.locked_until - t).total_seconds()) + 1)
    ok = security.verify_password(user.password_hash if user else None, body.password)
    if not ok:  # UC-03 EX1: однакова відповідь для невідомого e-mail і невірного пароля
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= security.LOCK_AFTER_FAILURES:
                user.locked_until = t + security.LOCK_DURATION
                user.failed_login_count = 0
        audit.append(
            db,
            action=AuditAction.LOGIN_FAILED,
            result=AuditResult.DENIED,
            object_type="user",
            object_id=user.id if user else "unknown",
            request_id=rid,
            actor_id=user.id if user else None,
        )
        db.commit()
        raise ApiError(401, "INVALID_CREDENTIALS")
    if user.status != AccountStatus.ACTIVE:  # UC-03 EX2 — лише після правильного пароля
        raise ApiError(403, "ACCOUNT_NOT_ACTIVE", status=user.status.value)
    user.failed_login_count = 0
    user.locked_until = None
    result = open_session(db, user, response)
    audit.append(
        db,
        action=AuditAction.LOGIN,
        object_type="user",
        object_id=user.id,
        request_id=rid,
        actor_id=user.id,
        actor_role=user.role,
        on_behalf_of_id=user.organization_id,
    )
    db.commit()
    return result


def check_csrf(request: Request) -> None:
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get("x-csrf-token")
    if not cookie or not header or cookie != header:
        raise ApiError(403, "CSRF_FAILED")
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != get_settings().frontend_base_url.rstrip("/"):
        raise ApiError(403, "CSRF_FAILED")


def session_from_cookie(db: Session, request: Request) -> UserSession | None:
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        return None
    return db.scalars(
        select(UserSession).where(UserSession.refresh_token_hash == security.token_hash(token))
    ).first()


@router.post("/refresh")
def refresh(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    check_csrf(request)
    s = session_from_cookie(db, request)
    if s is None or s.expires_at <= now():
        raise ApiError(401, "SESSION_EXPIRED")
    if s.revoked_at is not None:
        raise ApiError(401, "SESSION_REVOKED")
    user = db.get(User, s.user_id)
    if user.status != AccountStatus.ACTIVE:
        raise ApiError(401, "ACCOUNT_NOT_ACTIVE", status=user.status.value)
    s.revoked_at = now()  # ротація: старий токен оновлення більше не діє
    result = open_session(db, user, response)
    db.commit()
    return result


@router.post("/logout", status_code=204)
def logout(request: Request, db: Session = Depends(get_db)) -> Response:
    check_csrf(request)
    s = session_from_cookie(db, request)
    if s is not None and s.revoked_at is None:
        s.revoked_at = now()
        db.commit()
    response = Response(status_code=204)
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response


def new_clarification_token(db: Session, user: User) -> str:
    return issue_token(db, user, "REGISTRATION_CLARIFY", CLARIFY_TTL)
