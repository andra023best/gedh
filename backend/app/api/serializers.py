"""Перетворення моделей на JSON за схемами контракту (docs/openapi.yaml → components/schemas)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import consent
from app.models.enums import ConsentAction
from app.models.identity import ConsentRecord, Organization, RegistrationRequest, User

# Що зберігається, навіщо й скільки — табл. 3.26 звіту (UC-21 крок 2)
RETENTION = [
    {
        "data": "ПІБ, e-mail, хеш пароля, країна, організація, посада, мета",
        "purpose": "надання доступу; виконання умов користування (GDPR ст. 6(1)(b))",
        "retention": "поки обліковий запис активний + 30 днів, потім анонімізація",
    },
    {
        "data": "Документ верифікації",
        "purpose": "перевірка належності до організації",
        "retention": "до рішення адміністратора + 30 днів",
    },
    {
        "data": "Записи згоди (версія, час, IP-адреса)",
        "purpose": "доказ наданої згоди (GDPR ст. 7(1))",
        "retention": "строк облікового запису + 3 роки",
    },
    {
        "data": "Заявки, дозволи, угоди, факти вивантаження",
        "purpose": "керування доступом; угода про обмін даними",
        "retention": "3 роки",
    },
    {
        "data": "Ідентифікатор у журналі аудиту (без ПІБ, e-mail, IP)",
        "purpose": "підзвітність і безпека",
        "retention": "3 роки; псевдонімізація після видалення облікового запису",
    },
]


def iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def org_public(org: Organization | None) -> dict | None:
    if org is None:
        return None
    return {"id": str(org.id), "name": org.name, "type": org.type.value, "country_code": org.country_code}


def org_full(org: Organization) -> dict:
    return {
        **org_public(org),
        "email_domain": org.email_domain,
        "status": org.status.value,
        "verified_at": iso(org.verified_at),
    }


def user_brief(db: Session, user: User) -> dict:
    org = db.get(Organization, user.organization_id) if user.organization_id else None
    return {
        "id": str(user.id),
        "full_name": user.full_name,
        "role": user.role.value,
        "status": user.status.value,
        "organization": org_public(org),
    }


def me(db: Session, user: User) -> dict:
    last = db.scalars(
        select(ConsentRecord)
        .where(ConsentRecord.user_id == user.id, ConsentRecord.action == ConsentAction.GIVEN)
        .order_by(ConsentRecord.recorded_at.desc())
    ).first()
    return {
        **user_brief(db, user),
        "email": user.email,
        "position": user.position,
        "country_code": user.country_code,
        "created_at": iso(user.created_at),
        "consent": {
            "accepted_version": last.text_version if last else None,
            "current_version": consent.CURRENT_VERSION,
            "accepted_at": iso(last.recorded_at) if last else None,
        },
        "retention": RETENTION,
    }


def registration(db: Session, rr: RegistrationRequest) -> dict:
    user = db.get(User, rr.user_id)
    requested = db.get(Organization, rr.requested_organization_id) if rr.requested_organization_id else None
    current = db.get(Organization, user.organization_id) if user.organization_id else None
    org = current or requested
    domain_match = None
    if org is not None and org.email_domain:
        domain_match = user.email.lower().endswith("@" + org.email_domain.lower())
    return {
        "id": str(rr.id),
        "status": rr.status.value,
        "account_type": rr.account_type.value,
        "purpose": rr.purpose,
        "submitted_at": iso(rr.submitted_at),
        "decided_at": iso(rr.decided_at),
        "decision_reason": rr.decision_reason,
        "has_document": rr.verification_document_ref is not None,
        "email_domain_matches_organization": domain_match,  # FR-04: підказка адміністратору
        "organization_name": rr.requested_organization_name,
        "clarification_note": rr.clarification_note,
        "clarification_answer": rr.clarification_answer,
        "applicant": {
            "id": str(user.id),
            "full_name": user.full_name,
            "email": user.email,
            "position": user.position,
            "country_code": user.country_code,
            "status": user.status.value,
            "organization": org_public(org),
        },
    }
