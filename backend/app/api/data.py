"""Каталог (без значень) і перегляд значень набору з перевіркою доступу (контракт: tags reference, data).

Етап Е2+: доступ до відкритих наборів — будь-який активний обліковий запис (UC-16 крок 3).
Закриті набори: власник-постачальник і DG-офіцер — за роллю; решта — лише за чинним дозволом (етап Е4).
Поки дозволів немає, звернення інших до закритого набору → 403 ACCESS_DENIED із записом у журнал.
"""

import csv
import hashlib
import io
import json
import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.serializers import iso, org_public
from app.core import access, audit
from app.core.deps import api_user, get_db, request_id
from app.core.errors import ApiError
from app.ingestion.sources import SPECS
from app.models.datasets import (
    Dataset,
    DatasetVersion,
    Observation,
    OpenDataset,
    RestrictedDataset,
    SourceReference,
)
from app.models.enums import AuditAction, AuditResult, ExportChannel, ExportFormat, Role, VersionStatus
from app.models.exchange import AccessGrant, DataExport, data_export_versions
from app.models.identity import Organization, User
from app.models.ingestion import Connector, ImportRun, QualityReport

router = APIRouter(tags=["data"])
SOURCE_TITLES = {
    "WB": "World Bank — World Development Indicators",
    "IMF": "IMF — World Economic Outlook",
    "BIS": "BIS — Central bank policy rates",
}


def published(db: Session, dataset_id: uuid.UUID) -> DatasetVersion | None:
    return db.scalars(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset_id, DatasetVersion.status == VersionStatus.PUBLISHED)
        .order_by(DatasetVersion.version_no.desc())
    ).first()


def summary(db: Session, d: Dataset) -> dict:
    v = published(db, d.id)
    indicators = (
        sorted(
            db.scalars(select(Observation.indicator_code).where(Observation.version_id == v.id).distinct())
        )
        if v
        else []
    )
    owner = (
        db.get(Organization, d.owner_org_id) if isinstance(d, RestrictedDataset) and d.owner_org_id else None
    )
    return {
        "id": str(d.id),
        "kind": d.kind,
        "title_ua": d.title_ua,
        "title_en": d.title_en,
        "classification": d.classification.value,
        "licence": d.licence,
        "is_synthetic": d.is_synthetic,  # позначка «СИНТЕТИЧНІ ДАНІ» (FR-27, DG-04)
        "source_system": getattr(d, "source_system", None),
        "country_code": getattr(d, "country_code", None),
        "category_code": getattr(d, "category_code", None),
        "owner": org_public(owner),
        "indicators": indicators,
        "current_version_no": v.version_no if v else None,
        "updated_at": iso(v.published_at) if v else None,
    }


@router.get("/catalog/datasets")
def list_datasets(
    country: str | None = None,
    category: str | None = None,
    kind: str | None = Query(None, pattern="^(OPEN|RESTRICTED)$"),
    _: User = Depends(api_user),  # S-36: каталог — лише після входу
    db: Session = Depends(get_db),
) -> list[dict]:
    q = select(Dataset).order_by(Dataset.kind, Dataset.title_ua)
    if kind:
        q = q.where(Dataset.kind == kind)
    rows = db.scalars(q).all()
    if country:
        rows = [d for d in rows if getattr(d, "country_code", None) in (None, country)]
    if category:
        rows = [d for d in rows if getattr(d, "category_code", None) == category]
    return [summary(db, d) for d in rows]


@router.get("/catalog/datasets/{dataset_id}")
def get_dataset(dataset_id: uuid.UUID, _: User = Depends(api_user), db: Session = Depends(get_db)) -> dict:
    d = db.get(Dataset, dataset_id)
    if d is None:
        raise ApiError(404, "NOT_FOUND")
    versions = db.scalars(
        select(DatasetVersion)
        .where(
            DatasetVersion.dataset_id == d.id,
            DatasetVersion.status.in_([VersionStatus.PUBLISHED, VersionStatus.SUPERSEDED]),
        )
        .order_by(DatasetVersion.version_no.desc())
    )
    return {
        **summary(db, d),
        "status": getattr(d, "status", None) and d.status.value,
        "versions": [
            {
                "id": str(v.id),
                "version_no": v.version_no,
                "status": v.status.value,
                "published_at": iso(v.published_at),
            }
            for v in versions
        ],
    }


def source_info(db: Session, d: Dataset, v: DatasetVersion) -> dict:
    if isinstance(d, OpenDataset):
        run = db.scalars(select(ImportRun).where(ImportRun.produced_version_id == v.id)).first()
        connector = db.get(Connector, run.connector_id) if run else None
        code = connector.name.split()[-1] if connector else None
        series = SPECS[code].series if code in SPECS else ""
        return {
            "document_title": f"{SOURCE_TITLES.get(d.source_system, d.source_system)} ({series})",
            "url": run.request_url if run else None,
            "methodology": "Автоматичний імпорт з API джерела; гармонізація кодів країн (ISO 3166), одиниць, "
            "масштабу й частоти; перевірка якості Q-02 – Q-10",
            "responsible_person": "конектор GEDH",
            "document_date": iso(run.finished_at) if run else None,
            "raw_sha256": run.raw_payload_hash if run else None,
        }
    src = db.get(SourceReference, v.id)
    if src is None:
        return {}
    return {
        "document_title": src.document_title,
        "document_number": src.document_number,
        "document_date": iso(src.document_date),
        "url": src.url,
        "internal_ref": src.internal_ref,
        "methodology": src.methodology,
        "responsible_person": src.responsible_person,
    }


SYNTHETIC_NOTICE = "СИНТЕТИЧНІ ДАНІ — згенеровано для демонстрації, не є офіційною статистикою (D-04)"


def load_for_user(
    db: Session,
    request: Request,
    user: User,
    dataset_id: uuid.UUID,
    *,
    action: AuditAction,
    countries: str | None,
    period_from: date | None,
    period_to: date | None,
    version: int | None = None,
):
    """Спільна частина перегляду й вивантаження: AccessPolicy (UC-16) → версія → значення в межах дозволу."""
    d = db.get(Dataset, dataset_id)
    if d is None:
        raise ApiError(404, "NOT_FOUND")
    decision = access.authorize(db, user, d, period_from, period_to)
    if not decision.allowed:  # UC-16 EX2: відмова з причиною і записом у журнал
        audit.append(
            db,
            action=action,
            result=AuditResult.DENIED,
            object_type="dataset",
            object_id=d.id,
            request_id=request_id(request),
            actor_id=user.id,
            actor_role=user.role,
            on_behalf_of_id=user.organization_id,
            details={"reason": decision.reason},
        )
        db.commit()
        raise ApiError(403, decision.reason)
    if version:
        v = db.scalars(
            select(DatasetVersion).where(
                DatasetVersion.dataset_id == d.id, DatasetVersion.version_no == version
            )
        ).first()
        if v is not None and decision.basis == "GRANT" and v.status != VersionStatus.PUBLISHED:
            v = None  # за дозволом — лише чинна опублікована версія
    else:
        v = published(db, d.id)
    if v is None:
        raise ApiError(404, "NOT_FOUND")
    q = select(Observation).where(Observation.version_id == v.id)
    if countries:
        q = q.where(Observation.country_code.in_([c.strip().upper() for c in countries.split(",")]))
    if period_from:
        q = q.where(Observation.period_end >= period_from)
    if period_to:
        q = q.where(Observation.period_start <= period_to)
    obs = db.scalars(
        q.order_by(
            Observation.country_code,
            Observation.indicator_code,
            Observation.breakdown,
            Observation.period_start,
        )
    ).all()
    coverage = "FULL"
    if decision.period:  # видаємо лише те, що покриває дозвіл (UC-16 AC1)
        lo, hi = decision.period
        inside = [o for o in obs if o.period_start >= lo and o.period_end <= hi]
        if len(inside) < len(obs):
            coverage = "PARTIAL"
        obs = inside
    return d, v, obs, decision, coverage


def obs_json(o: Observation) -> dict:
    return {
        "country": o.country_code,
        "indicator": o.indicator_code,
        "breakdown": o.breakdown,
        "period": {"start": o.period_start.isoformat(), "end": o.period_end.isoformat()},
        "value": o.value,
        "note": o.note,
    }


@router.get("/datasets/{dataset_id}/observations")
def observations(
    dataset_id: uuid.UUID,
    request: Request,
    countries: str | None = None,
    period_from: date | None = None,
    period_to: date | None = None,
    version: int | None = Query(None, ge=1),
    user: User = Depends(api_user),
    db: Session = Depends(get_db),
) -> dict:
    d, v, obs, decision, coverage = load_for_user(
        db,
        request,
        user,
        dataset_id,
        action=AuditAction.DATA_VIEW,
        countries=countries,
        period_from=period_from,
        period_to=period_to,
        version=version,
    )
    report = db.scalars(select(QualityReport).where(QualityReport.version_id == v.id)).first()
    audit.append(
        db,
        action=AuditAction.DATA_VIEW,
        object_type="dataset",
        object_id=d.id,
        request_id=request_id(request),
        actor_id=user.id,
        actor_role=user.role,
        on_behalf_of_id=user.organization_id,
        details={
            "version_no": v.version_no,
            "records": len(obs),
            "basis": decision.basis,
            "grant_id": str(decision.grant.id) if decision.grant else None,
            "coverage": coverage,
            "channel": getattr(request.state, "channel", "WEB"),
        },
    )
    db.commit()
    return {
        "dataset_id": str(d.id),
        "title_ua": d.title_ua,
        "version_no": v.version_no,
        "published_at": iso(v.published_at),
        "is_synthetic": d.is_synthetic,
        "synthetic_notice": SYNTHETIC_NOTICE if d.is_synthetic else None,
        "coverage": coverage,
        "covered_period": {"start": iso(decision.period[0]), "end": iso(decision.period[1])}
        if decision.period
        else None,
        "grant_id": str(decision.grant.id) if decision.grant else None,
        "grant_valid_to": iso(decision.grant.valid_to) if decision.grant else None,
        "licence": d.licence,
        "quality": {"score": report.score, "warnings": report.warning_count} if report else None,
        "source": source_info(db, d, v),
        "items": [obs_json(o) for o in obs],
    }


def csv_safe(text: str | None) -> str:
    """Захист від CSV injection: текст, що починається з = + - @, Excel не виконає як формулу."""
    if not text:
        return ""
    return "'" + text if text[0] in "=+-@\t\r" else text


@router.get("/datasets/{dataset_id}/export")
def export(
    dataset_id: uuid.UUID,
    request: Request,
    format: str = Query(pattern="^(csv|json)$"),
    countries: str | None = None,
    period_from: date | None = None,
    period_to: date | None = None,
    user: User = Depends(api_user),
    db: Session = Depends(get_db),
) -> Response:
    """UC-07, FR-11, FR-27: файл з метаданими й SHA-256; для закритих — ідентифікатор дозволу й позначка."""
    d, v, obs, decision, coverage = load_for_user(
        db,
        request,
        user,
        dataset_id,
        action=AuditAction.DATA_DOWNLOAD,
        countries=countries,
        period_from=period_from,
        period_to=period_to,
    )
    generated = datetime.now(UTC)
    src = source_info(db, d, v)
    meta = {
        "dataset_id": str(d.id),
        "title": d.title_ua,
        "version_no": v.version_no,
        "published_at": iso(v.published_at),
        "generated_at": generated.isoformat(),
        "licence": d.licence,
        "source": src.get("document_title"),
        "is_synthetic": d.is_synthetic,
        "synthetic_notice": SYNTHETIC_NOTICE if d.is_synthetic else None,
        "grant_id": str(decision.grant.id) if decision.grant else None,
        "coverage": coverage,
        "covered_period": [iso(decision.period[0]), iso(decision.period[1])] if decision.period else None,
    }
    if format == "json":
        body = json.dumps(
            {"metadata": meta, "observations": [obs_json(o) for o in obs]}, ensure_ascii=False, indent=1
        ).encode("utf-8")
        media = "application/json"
    else:
        buf = io.StringIO()
        for k, val in meta.items():  # метадані — рядками-коментарями на початку файлу
            if val is not None:
                buf.write(f"# {k}: {val}\n")
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["country", "indicator", "breakdown", "period_start", "period_end", "value", "note"])
        for o in obs:
            w.writerow(
                [
                    o.country_code,
                    o.indicator_code,
                    csv_safe(o.breakdown),
                    o.period_start.isoformat(),
                    o.period_end.isoformat(),
                    "" if o.value is None else o.value,
                    csv_safe(o.note),
                ]
            )
        body = ("﻿" + buf.getvalue()).encode("utf-8")  # BOM — щоб Excel показав кирилицю
        media = "text/csv; charset=utf-8"
    sha = hashlib.sha256(body).hexdigest()
    exp = DataExport(
        format=ExportFormat(format.upper()),
        # FR-12: вивантаження через API-ключ позначається каналом API
        channel=ExportChannel.API if getattr(request.state, "channel", None) == "API" else ExportChannel.WEB,
        records_count=len(obs),
        file_hash=sha,
        grant_id=decision.grant.id if decision.grant else None,
        exported_by_id=user.id,
    )
    db.add(exp)
    db.flush()
    db.execute(data_export_versions.insert().values(export_id=exp.id, version_id=v.id))
    audit.append(
        db,
        action=AuditAction.DATA_DOWNLOAD,
        object_type="dataset",
        object_id=d.id,
        request_id=request_id(request),
        actor_id=user.id,
        actor_role=user.role,
        on_behalf_of_id=user.organization_id,
        details={
            "export_id": str(exp.id),
            "format": format,
            "records": len(obs),
            "file_sha256": sha,
            "grant_id": meta["grant_id"],
            "version_no": v.version_no,
            "coverage": coverage,
        },
    )
    db.commit()
    name = f"gedh-{d.id.hex[:8]}-v{v.version_no}-{generated:%Y%m%d}.{format}"
    headers = {
        "Content-Disposition": f'attachment; filename="{name}"',
        "X-Content-SHA256": sha,
        "X-Coverage": coverage,
    }
    if decision.grant:
        headers["X-Grant-Id"] = str(decision.grant.id)
    return Response(content=body, media_type=media, headers=headers)


@router.get("/coverage")
def coverage(_: User = Depends(api_user), db: Session = Depends(get_db)) -> list[dict]:
    """Карта покриття (UC-04, FR-06): скільки й які показники мають дані в країні; після входу (S-36)."""
    current = (
        select(DatasetVersion.id)
        .join(OpenDataset, OpenDataset.id == DatasetVersion.dataset_id)
        .where(DatasetVersion.status == VersionStatus.PUBLISHED)
    )
    rows = db.execute(
        select(Observation.country_code, func.array_agg(func.distinct(Observation.indicator_code)))
        .where(Observation.version_id.in_(current), Observation.value.is_not(None))
        .group_by(Observation.country_code)
    ).all()
    # UC-04 крок 3 і 5: кількість і перелік показників (без значень) — для підказки й панелі гостя на карті
    return [{"country": c, "indicators": len(codes), "codes": sorted(codes)} for c, codes in sorted(rows)]


# ---------------------------------------------------------------------------- походження (FR-33)
@router.get("/datasets/{dataset_id}/lineage")
def lineage(
    dataset_id: uuid.UUID, request: Request, user: User = Depends(api_user), db: Session = Depends(get_db)
) -> dict:
    """FR-33, DG-16, UC-05 крок 6, UC-15 AC2: джерело → імпорт / завантаження → перевірка → версія →
    дозволи → вивантаження.

    Хто бачить: відкритий набір — будь-який активний користувач; закритий — той, хто має доступ до значень
    (AccessPolicy: власник, DG-офіцер, чинний дозвіл), інакше 403 із записом у журнал.
    Дозволи й вивантаження інших організацій бачать лише DG-офіцер і організація-власник;
    отримувач — лише свої.
    """
    d = db.get(Dataset, dataset_id)
    if d is None:
        raise ApiError(404, "NOT_FOUND")
    restricted = isinstance(d, RestrictedDataset)
    rid = request_id(request)
    if restricted:
        decision = access.authorize(db, user, d, None, None)
        if not decision.allowed:
            audit.append(
                db,
                action=AuditAction.DATA_VIEW,
                result=AuditResult.DENIED,
                object_type="dataset",
                object_id=d.id,
                request_id=rid,
                actor_id=user.id,
                actor_role=user.role,
                on_behalf_of_id=user.organization_id,
                details={"view": "lineage", "reason": decision.reason},
            )
            db.commit()
            raise ApiError(403, decision.reason)
    sees_all = user.role == Role.DG_OFFICER or (restricted and user.organization_id == d.owner_org_id)

    def org_name(org_id) -> str | None:
        o = db.get(Organization, org_id) if org_id else None
        return o.name if o else None

    def role_of(user_id) -> str | None:
        u = db.get(User, user_id) if user_id else None
        return u.role.value if u else None

    grants_q = select(AccessGrant).where(AccessGrant.dataset_id == d.id).order_by(AccessGrant.valid_from)
    if not sees_all:
        grants_q = grants_q.where(AccessGrant.grantee_id == user.id)
    grants = [
        {
            "id": str(g.id),
            "organization": org_name(db.get(User, g.grantee_id).organization_id),
            "period": [iso(g.period_start), iso(g.period_end)],
            "valid_from": iso(g.valid_from),
            "valid_to": iso(g.valid_to),
            "status": g.status.value,
            "suspension_reason": g.suspension_reason,
            "deletion_confirmed_at": iso(g.deletion_confirmed_at),
        }
        for g in db.scalars(grants_q)
    ]
    versions = []
    for v in db.scalars(
        select(DatasetVersion).where(DatasetVersion.dataset_id == d.id).order_by(DatasetVersion.version_no)
    ):
        qr = db.scalars(select(QualityReport).where(QualityReport.version_id == v.id)).first()
        prev = db.get(DatasetVersion, v.previous_id) if v.previous_id else None
        exports_q = (
            select(DataExport)
            .join(data_export_versions, data_export_versions.c.export_id == DataExport.id)
            .where(data_export_versions.c.version_id == v.id)
            .order_by(DataExport.created_at)
        )
        if not sees_all:
            exports_q = exports_q.where(DataExport.exported_by_id == user.id)
        versions.append(
            {
                "version_no": v.version_no,
                "status": v.status.value,
                "created_at": iso(v.created_at),
                "source": source_info(db, d, v),  # 1–2: першоджерело / запуск конектора і хеш сирих даних
                "file_sha256": v.file_hash,
                "uploaded_by_role": role_of(v.uploaded_by_id),
                "quality": {  # 3: DQ-звіт
                    "score": qr.score,
                    "records": qr.records_total,
                    "critical": qr.critical_count,
                    "warnings": qr.warning_count,
                    "warnings_confirmed_at": iso(qr.warnings_confirmed_at),
                }
                if qr
                else None,
                "published_at": iso(v.published_at),  # 4: рішення про публікацію — хто (роль) і коли
                "published_by_role": role_of(v.published_by_id),
                "rejection_reason": v.rejection_reason,
                "previous_version_no": prev.version_no if prev else None,
                "exports": [  # 6: вивантаження з хешами файлів
                    {
                        "at": iso(x.created_at),
                        "format": x.format.value,
                        "records": x.records_count,
                        "file_sha256": x.file_hash,
                        "grant_id": str(x.grant_id) if x.grant_id else None,
                        "organization": org_name(db.get(User, x.exported_by_id).organization_id),
                    }
                    for x in db.scalars(exports_q)
                ],
            }
        )
    audit.append(
        db,
        action=AuditAction.DATA_VIEW,
        object_type="dataset",
        object_id=d.id,
        request_id=rid,
        actor_id=user.id,
        actor_role=user.role,
        on_behalf_of_id=user.organization_id,
        details={"view": "lineage", "versions": len(versions), "scope": "all" if sees_all else "own"},
    )
    db.commit()
    return {
        "dataset": summary(db, d),
        "scope": "all" if sees_all else "own",
        "versions": versions,
        "grants": grants,  # 5: дозволи, що поширюються на набір
    }
