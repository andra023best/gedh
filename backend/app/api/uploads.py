"""Завантаження закритих наборів постачальником і перевірка DG-офіцером (UC-12, UC-13, UC-18).

FR-21: файл лише за шаблоном і лише з першоджерелом. FR-22: перевірка Q-01 – Q-11 і DQ-звіт.
FR-23: публікація лише після перевірки першоджерела й DQ-звіту DG-офіцером.
D-04: на демо-платформі всі закриті набори позначаються як синтетичні.
"""

import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import exchange
from app.api.serializers import iso
from app.core import audit, notifier
from app.core.deps import current_user, get_db, request_id, require
from app.core.errors import ApiError
from app.ingestion import quality, upload
from app.ingestion.service import indicator_rules, snapshot
from app.models.datasets import DatasetVersion, Observation, RestrictedDataset, SourceReference
from app.models.enums import (
    AccountStatus,
    AuditAction,
    AuditResult,
    Classification,
    DatasetStatus,
    Role,
    VersionStatus,
)
from app.models.identity import Organization, User
from app.models.ingestion import QualityIssue, QualityReport, QualityRule
from app.models.reference import Country, DataCategory
from app.models.technical import StoredFile

router = APIRouter(tags=["uploads"])
provider_only = require(Role.DATA_PROVIDER)
dg_only = require(Role.DG_OFFICER)

DATASET_MAX = 5 * 1024 * 1024  # NFR-19
REJECTED_FILE_KEEP = timedelta(days=30)  # табл. 3.26: відхилені файли карантину — 30 днів
# Сигнатури форматів, які точно не є CSV/JSON (Excel, ZIP, PDF, зображення) — NFR-19
BINARY_SIGNATURES = (b"PK\x03\x04", b"%PDF", b"\xd0\xcf\x11\xe0", b"\x89PNG", b"\xff\xd8\xff", b"GIF8")


# ---------------------------------------------------------------------------- шаблони
@router.get("/templates/upload.csv")
def csv_template() -> Response:
    return Response(
        content="﻿" + upload.CSV_TEMPLATE,  # BOM — щоб Excel відкрив кирилицю
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="gedh-upload-template.csv"'},
    )


@router.get("/templates/upload.schema.json")
def json_schema() -> dict:
    return upload.SCHEMA


# ---------------------------------------------------------------------------- серіалізація
def version_out(db: Session, v: DatasetVersion, *, with_issues: bool = True) -> dict:
    d = db.get(RestrictedDataset, v.dataset_id)
    src = db.get(SourceReference, v.id)
    qr = db.scalars(select(QualityReport).where(QualityReport.version_id == v.id)).first()
    issues = []
    if qr and with_issues:
        severity = {r.code: r.severity.value for r in db.scalars(select(QualityRule))}  # рівень — з правила
        rows = db.scalars(
            select(QualityIssue).where(QualityIssue.report_id == qr.id).order_by(QualityIssue.id).limit(300)
        )
        issues = [
            {
                "rule_code": i.rule_code,
                "severity": severity.get(i.rule_code, "WARNING"),
                "record_ref": i.record_ref,
                "message": i.message,
            }
            for i in rows
        ]
    uploader = db.get(User, v.uploaded_by_id) if v.uploaded_by_id else None
    return {
        "id": str(v.id),
        "dataset_id": str(v.dataset_id),
        "dataset_title_ua": d.title_ua if d else None,
        "country_code": d.country_code if d else None,
        "category_code": d.category_code if d else None,
        "is_synthetic": d.is_synthetic if d else None,
        "version_no": v.version_no,
        "status": v.status.value,
        "file_hash": v.file_hash,
        "created_at": iso(v.created_at),
        "published_at": iso(v.published_at),
        "rejection_reason": v.rejection_reason,
        "previous_version_id": str(v.previous_id) if v.previous_id else None,
        "uploaded_by": uploader.full_name if uploader else None,
        "records": db.scalar(select(func.count(Observation.id)).where(Observation.version_id == v.id)),
        "source": None
        if src is None
        else {
            "document_title": src.document_title,
            "document_number": src.document_number,
            "document_date": iso(src.document_date),
            "url": src.url,
            "internal_ref": src.internal_ref,
            "methodology": src.methodology,
            "responsible_person": src.responsible_person,
        },
        "quality_report": None
        if qr is None
        else {
            "records_total": qr.records_total,
            "critical_count": qr.critical_count,
            "warning_count": qr.warning_count,
            "score": qr.score,
            "warnings_confirmed_at": iso(qr.warnings_confirmed_at),
            "issues": issues,
        },
    }


def get_version(db: Session, version_id: uuid.UUID) -> DatasetVersion:
    v = db.get(DatasetVersion, version_id)
    if v is None or db.get(RestrictedDataset, v.dataset_id) is None:
        raise ApiError(404, "NOT_FOUND")
    return v


def published_version(db: Session, dataset_id: uuid.UUID) -> DatasetVersion | None:
    return db.scalars(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset_id, DatasetVersion.status == VersionStatus.PUBLISHED)
        .order_by(DatasetVersion.version_no.desc())
    ).first()


def notify_role(db: Session, role: Role, subject: str, text: str, link: str | None = None) -> None:
    users = db.scalars(select(User).where(User.role == role, User.status == AccountStatus.ACTIVE)).all()
    notifier.notify(db, users, subject, text, link=link)


# ---------------------------------------------------------------------------- постачальник
@router.get("/provider/datasets")
def provider_datasets(user: User = Depends(provider_only), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(RestrictedDataset)
        .where(RestrictedDataset.owner_org_id == user.organization_id)
        .order_by(RestrictedDataset.created_at)
    ).all()
    out = []
    for d in rows:
        versions = db.scalars(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == d.id)
            .order_by(DatasetVersion.version_no.desc())
        ).all()
        out.append(
            {
                "id": str(d.id),
                "title_ua": d.title_ua,
                "title_en": d.title_en,
                "country_code": d.country_code,
                "category_code": d.category_code,
                "status": d.status.value,
                "is_synthetic": d.is_synthetic,
                "versions": [
                    {
                        "id": str(v.id),
                        "version_no": v.version_no,
                        "status": v.status.value,
                        "created_at": iso(v.created_at),
                        "published_at": iso(v.published_at),
                    }
                    for v in versions
                ],
            }
        )
    return out


def source_problems(title: str, methodology: str, person: str, number, doc_date, url, internal_ref) -> bool:
    """Q-11: першоджерело з реквізитами — назва, методика, відповідальна особа й хоча б один ідентифікатор."""
    has_id = bool((number and doc_date) or url or internal_ref)
    return not (title.strip() and methodology.strip() and person.strip() and has_id)


@router.post("/provider/uploads", status_code=201)
async def upload_dataset(
    request: Request,
    file: Annotated[UploadFile, File()],
    category_code: Annotated[str, Form()],
    source_document_title: Annotated[str, Form(max_length=300)] = "",
    source_methodology: Annotated[str, Form(max_length=4000)] = "",
    source_responsible_person: Annotated[str, Form(max_length=200)] = "",
    source_document_number: Annotated[str | None, Form(max_length=100)] = None,
    source_document_date: Annotated[date | None, Form()] = None,
    source_url: Annotated[str | None, Form(max_length=500)] = None,
    source_internal_ref: Annotated[str | None, Form(max_length=200)] = None,
    dataset_id: Annotated[uuid.UUID | None, Form()] = None,
    title_ua: Annotated[str | None, Form(max_length=300)] = None,
    title_en: Annotated[str | None, Form(max_length=300)] = None,
    user: User = Depends(provider_only),
    db: Session = Depends(get_db),
) -> dict:
    rid = request_id(request)
    org = db.get(Organization, user.organization_id) if user.organization_id else None
    if org is None:
        raise ApiError(403, "ORGANIZATION_NOT_VERIFIED")
    # Q-11 / UC-12 EX1: без першоджерела файл не приймається
    if source_problems(
        source_document_title,
        source_methodology,
        source_responsible_person,
        source_document_number,
        source_document_date,
        source_url,
        source_internal_ref,
    ):
        raise ApiError(
            422,
            "SOURCE_REQUIRED",
            "Назва документа, методика, відповідальна особа "
            "й номер з датою, посилання або внутрішній реєстраційний номер",
        )
    if source_url and not source_url.startswith(("https://", "http://")):
        raise ApiError(422, "INVALID_SOURCE_URL")

    data = await file.read(DATASET_MAX + 1)
    if len(data) > DATASET_MAX:
        raise ApiError(413, "FILE_TOO_LARGE", max_bytes=DATASET_MAX)
    if not data or data.startswith(BINARY_SIGNATURES):
        raise ApiError(415, "UNSUPPORTED_FILE_TYPE", allowed=["CSV", "JSON"])
    sha = hashlib.sha256(data).hexdigest()
    duplicate = db.scalar(
        select(DatasetVersion.id)
        .join(RestrictedDataset, RestrictedDataset.id == DatasetVersion.dataset_id)
        .where(DatasetVersion.file_hash == sha)
    )
    if duplicate:
        raise ApiError(409, "DUPLICATE_FILE", existing_id=str(duplicate))  # UC-12 EX3

    # набір: новий або нова версія наявного (лише своєї організації)
    if dataset_id:
        dataset = db.get(RestrictedDataset, dataset_id)
        if dataset is None or dataset.owner_org_id != org.id:
            raise ApiError(403, "FORBIDDEN_ROLE", "Можна оновлювати лише набори своєї організації")
        if dataset.status == DatasetStatus.WITHDRAWN:
            raise ApiError(409, "INVALID_STATE", status=dataset.status.value)
        category_code = dataset.category_code
    else:
        if db.get(DataCategory, category_code) is None:
            raise ApiError(422, "CATEGORY_NOT_FOUND")
        if not (title_ua and title_ua.strip() and title_en and title_en.strip()):
            raise ApiError(422, "TITLE_REQUIRED")
        dataset = RestrictedDataset(
            title_ua=title_ua.strip(),
            title_en=title_en.strip(),
            classification=Classification.RESTRICTED,
            licence="GEDH DSA (угода про обмін даними)",
            is_synthetic=True,  # D-04
            status=DatasetStatus.DRAFT,
            country_code=org.country_code,
            category_code=category_code,
            owner_org_id=org.id,
        )
        db.add(dataset)
        db.flush()

    # перевірка якості: Q-01 / Q-04 (розбір) + Q-02 – Q-10 (рушій) + країна організації (UC-12 передумова)
    rows, issues = upload.parse(data)
    countries = set(db.scalars(select(Country.code)))
    previous = published_version(db, dataset.id)
    report = quality.evaluate(rows, indicator_rules(db), countries, previous=snapshot(db, previous))
    report.issues = issues + report.issues
    report.total = max(upload.count_records(data), len(rows), 1)  # усі записи файлу, а не лише валідні
    foreign = [r for r in rows if r.country != dataset.country_code]
    for r in foreign[: upload.MAX_ISSUES_PER_RULE]:
        report.issues.append(
            quality.Issue(
                "Q-03",
                quality.Severity.CRITICAL,
                r.ref(),
                f"Країна {r.country} ≠ країна організації {dataset.country_code}",
            )
        )
    now = datetime.now(UTC)
    next_no = (
        db.scalar(select(func.max(DatasetVersion.version_no)).where(DatasetVersion.dataset_id == dataset.id))
        or 0
    ) + 1
    if report.has_critical:
        status, reason = VersionStatus.REJECTED, "Критичні порушення якості — див. DQ-звіт"
    elif report.warnings:
        status, reason = VersionStatus.QUARANTINE, None  # чекає підтвердження попереджень (UC-12 AC1)
    else:
        status, reason = VersionStatus.PENDING_REVIEW, None
    version = DatasetVersion(
        dataset_id=dataset.id,
        version_no=next_no,
        status=status,
        file_hash=sha,
        uploaded_by_id=user.id,
        rejection_reason=reason,
    )
    db.add(version)
    db.flush()
    db.add(
        SourceReference(
            version_id=version.id,
            document_title=source_document_title.strip(),
            document_number=source_document_number or None,
            document_date=source_document_date,
            url=source_url or None,
            internal_ref=source_internal_ref or None,
            methodology=source_methodology.strip(),
            responsible_person=source_responsible_person.strip(),
        )
    )
    db.add(
        StoredFile(
            owner_id=user.id,
            purpose="DATASET_UPLOAD",
            content_type=file.content_type or "text/plain",
            size=len(data),
            sha256=sha,
            data=data,
            delete_after=now + REJECTED_FILE_KEEP if status == VersionStatus.REJECTED else None,
        )
    )
    if status != VersionStatus.REJECTED:  # значення відхиленої версії не зберігаємо
        db.add_all(
            Observation(
                version_id=version.id,
                country_code=r.country,
                indicator_code=r.indicator,
                breakdown=r.breakdown,
                period_start=r.period_start,
                period_end=r.period_end,
                value=r.value,
                note=r.note,
            )
            for r in rows
        )
    qr = QualityReport(
        version_id=version.id,
        records_total=report.total,
        critical_count=len(report.critical),
        warning_count=len(report.warnings),
        score=report.score,
    )
    db.add(qr)
    db.flush()
    db.add_all(
        QualityIssue(report_id=qr.id, rule_code=i.rule, record_ref=i.ref[:100], message=i.message[:2000])
        for i in report.issues
    )
    audit.append(
        db,
        action=AuditAction.DATASET_UPLOAD,
        object_type="dataset_version",
        object_id=version.id,
        request_id=rid,
        actor_id=user.id,
        actor_role=user.role,
        on_behalf_of_id=org.id,
        result=AuditResult.SUCCESS if status != VersionStatus.REJECTED else AuditResult.DENIED,
        details={
            "dataset_id": str(dataset.id),
            "version_no": next_no,
            "status": status.value,
            "file_sha256": sha,
            "records": len(rows),
            "dq_score": qr.score,
        },
    )
    db.commit()
    if status == VersionStatus.PENDING_REVIEW:
        notify_role(
            db,
            Role.DG_OFFICER,
            "GEDH: новий набір на перевірку",
            f"Набір «{dataset.title_ua}», версія {next_no}, очікує перевірки першоджерела й публікації.",
            "/dg/review",
        )
    return version_out(db, version)


@router.get("/dataset-versions/{version_id}")
def get_dataset_version(
    version_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    v = get_version(db, version_id)
    d = db.get(RestrictedDataset, v.dataset_id)
    allowed = user.role in (Role.DG_OFFICER, Role.ADMIN) or (
        user.role == Role.DATA_PROVIDER and user.organization_id == d.owner_org_id
    )
    if not allowed:
        raise ApiError(403, "FORBIDDEN_ROLE")
    out = version_out(db, v)
    prev = published_version(db, v.dataset_id)
    if prev and prev.id != v.id:  # відмінності від попередньої опублікованої версії (UC-13 крок 2)
        old, new = snapshot(db, prev), snapshot(db, v)
        out["diff"] = {
            "previous_version_no": prev.version_no,
            "added": len(new.keys() - old.keys()),
            "removed": len(old.keys() - new.keys()),
            "changed": sum(1 for k in new.keys() & old.keys() if new[k] != old[k]),
        }
    return out


class CommentBody(BaseModel):
    comment: str = Field(min_length=5, max_length=2000)


@router.post("/dataset-versions/{version_id}/confirm-warnings")
def confirm_warnings(
    version_id: uuid.UUID,
    body: CommentBody,
    request: Request,
    user: User = Depends(provider_only),
    db: Session = Depends(get_db),
) -> dict:
    v = get_version(db, version_id)
    d = db.get(RestrictedDataset, v.dataset_id)
    if d.owner_org_id != user.organization_id:
        raise ApiError(403, "FORBIDDEN_ROLE")
    if v.status != VersionStatus.QUARANTINE:
        raise ApiError(409, "INVALID_STATE", status=v.status.value)
    qr = db.scalars(select(QualityReport).where(QualityReport.version_id == v.id)).one()
    qr.warnings_confirmed_at = datetime.now(UTC)
    v.status = VersionStatus.PENDING_REVIEW
    audit.append(
        db,
        action=AuditAction.DATASET_UPLOAD,
        object_type="dataset_version",
        object_id=v.id,
        request_id=request_id(request),
        actor_id=user.id,
        actor_role=user.role,
        on_behalf_of_id=user.organization_id,
        details={"step": "WARNINGS_CONFIRMED", "warnings": qr.warning_count, "comment": body.comment},
    )
    db.commit()
    notify_role(
        db,
        Role.DG_OFFICER,
        "GEDH: новий набір на перевірку",
        f"Набір «{d.title_ua}», версія {v.version_no} (з підтвердженими попередженнями), очікує перевірки.",
        "/dg/review",
    )
    return version_out(db, v)


# ---------------------------------------------------------------------------- DG-офіцер
@router.get("/dg/review-queue")
def review_queue(user: User = Depends(dg_only), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(DatasetVersion)
        .join(RestrictedDataset, RestrictedDataset.id == DatasetVersion.dataset_id)
        .where(DatasetVersion.status == VersionStatus.PENDING_REVIEW)
        .order_by(DatasetVersion.created_at)
    ).all()
    return [version_out(db, v, with_issues=False) for v in rows]


def notify_uploader(db: Session, v: DatasetVersion, subject: str, text: str) -> None:
    u = db.get(User, v.uploaded_by_id) if v.uploaded_by_id else None
    notifier.notify(db, [u], subject, text, link=f"/provider?version={v.id}")


@router.post("/dataset-versions/{version_id}/publish")
def publish(
    version_id: uuid.UUID, request: Request, user: User = Depends(dg_only), db: Session = Depends(get_db)
) -> dict:
    v = get_version(db, version_id)
    if v.status != VersionStatus.PENDING_REVIEW:
        raise ApiError(409, "INVALID_STATE", status=v.status.value)
    if v.uploaded_by_id == user.id:
        raise ApiError(403, "SELF_REVIEW_FORBIDDEN")  # D-01
    d = db.get(RestrictedDataset, v.dataset_id)
    prev = published_version(db, d.id)
    now = datetime.now(UTC)
    if prev is not None:
        prev.status = VersionStatus.SUPERSEDED  # попередня версія не змінюється (D-05)
        v.previous_id = prev.id  # lineage
    v.status, v.published_at, v.published_by_id = VersionStatus.PUBLISHED, now, user.id
    if d.status == DatasetStatus.DRAFT:
        d.status = DatasetStatus.ACTIVE
    db.flush()
    # UC-13 крок 5 / UC-10 AC1: заявки, що чекали на ці дані, отримують угоду
    waiting = exchange.offer_waiting_requests(db, d)
    audit.append(
        db,
        action=AuditAction.DATASET_PUBLISH,
        object_type="dataset_version",
        object_id=v.id,
        request_id=request_id(request),
        actor_id=user.id,
        actor_role=user.role,
        details={
            "dataset_id": str(d.id),
            "version_no": v.version_no,
            "previous_version_no": prev.version_no if prev else None,
            "agreements_offered": [r.number for r in waiting],
        },
    )
    db.commit()
    for r in waiting:
        exchange.notify(
            db,
            [db.get(User, r.requester_id)],
            f"GEDH: заявка {r.number}",
            "Дані опубліковано. Перегляньте й прийміть угоду про обмін даними (14 днів).",
            exchange.req_link(r),
        )
    notify_uploader(
        db,
        v,
        "GEDH: набір опубліковано",
        f"Набір «{d.title_ua}», версія {v.version_no}, опубліковано після перевірки DG-офіцером.",
    )
    return version_out(db, v)


class ReasonBody(BaseModel):
    reason: str = Field(min_length=5, max_length=2000)


@router.post("/dataset-versions/{version_id}/reject")
def reject(
    version_id: uuid.UUID,
    body: ReasonBody,
    request: Request,
    user: User = Depends(dg_only),
    db: Session = Depends(get_db),
) -> dict:
    v = get_version(db, version_id)
    if v.status != VersionStatus.PENDING_REVIEW:
        raise ApiError(409, "INVALID_STATE", status=v.status.value)
    d = db.get(RestrictedDataset, v.dataset_id)
    v.status, v.rejection_reason = VersionStatus.REJECTED, body.reason
    f = db.scalars(select(StoredFile).where(StoredFile.sha256 == v.file_hash)).first()
    if f is not None:
        f.delete_after = datetime.now(UTC) + REJECTED_FILE_KEEP
    audit.append(
        db,
        action=AuditAction.DATASET_REJECT,
        object_type="dataset_version",
        object_id=v.id,
        request_id=request_id(request),
        actor_id=user.id,
        actor_role=user.role,
        details={"dataset_id": str(d.id), "version_no": v.version_no},
    )
    db.commit()
    notify_uploader(
        db,
        v,
        "GEDH: набір відхилено",
        f"Набір «{d.title_ua}», версію {v.version_no}, відхилено DG-офіцером. Причина: {body.reason}",
    )
    return version_out(db, v)
