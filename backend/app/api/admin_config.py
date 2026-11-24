"""Конектори й довідники — адміністратор (UC-20; FR-17, FR-43; S-37).

Кожна зміна — `CONFIG_CHANGE` у журналі з автором, часом, старим і новим значенням (постумова UC-20).
Організації ведуться в admin.py (створення верифікованої, верифікація).
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serializers import iso
from app.core import audit
from app.core.db import new_session
from app.core.deps import get_db, request_id, require
from app.core.errors import ApiError
from app.ingestion import schedule, service, sources
from app.models.datasets import Dataset, Observation
from app.models.enums import AuditAction, Frequency, Role, RunTrigger
from app.models.identity import User
from app.models.ingestion import Connector, ImportRun
from app.models.reference import Country, DataCategory, Indicator

router = APIRouter(prefix="/admin", tags=["admin-config"])
admin_only = require(Role.ADMIN)
CSV_MAX = 5 * 1024 * 1024  # як для файлів постачальників (NFR-19)


def log_change(db: Session, request: Request, admin: User, obj: str, obj_id: str, changes: dict) -> None:
    audit.append(
        db,
        action=AuditAction.CONFIG_CHANGE,
        object_type=obj,
        object_id=obj_id,
        request_id=request_id(request),
        actor_id=admin.id,
        actor_role=admin.role,
        on_behalf_of_id=admin.organization_id,
        details={"changes": changes},
    )


def plain(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "value"):
        return v.value
    return v


def apply(target: Any, patch: dict) -> dict:
    """Змінює лише передані поля; повертає {поле: [старе, нове]} лише для тих, що справді змінилися."""
    changes = {}
    for field, new in patch.items():
        old = getattr(target, field)
        if plain(old) != plain(new):
            setattr(target, field, new)
            changes[field] = [plain(old), plain(new)]
    if not changes:
        raise ApiError(422, "NOTHING_TO_UPDATE")
    return changes


# ---------------------------------------------------------------------------- конектори (FR-17)
def run_out(r: ImportRun) -> dict:
    return {
        "id": str(r.id),
        "trigger": r.trigger.value,
        "started_at": iso(r.started_at),
        "finished_at": iso(r.finished_at),
        "status": r.status.value,
        "records": r.records_count,
        "request_url": r.request_url,
        "raw_sha256": r.raw_payload_hash,
        "version_id": str(r.produced_version_id) if r.produced_version_id else None,
        "error": r.error_message,
    }


def connector_out(db: Session, c: Connector, runs: int = 5) -> dict:
    last = db.scalars(
        select(ImportRun)
        .where(ImportRun.connector_id == c.id)
        .order_by(ImportRun.started_at.desc())
        .limit(runs)
    ).all()
    ds = db.get(Dataset, c.target_dataset_id)
    return {
        "id": str(c.id),
        "name": c.name,
        "indicator": c.name.split()[-1],
        "source_system": c.source_system,
        "base_url": c.base_url,
        "schedule": c.schedule,
        "is_enabled": c.is_enabled,
        "next_run": iso(schedule.next_due(c.schedule, date.today() - date.resolution))
        if c.is_enabled
        else None,
        "dataset_id": str(c.target_dataset_id),
        "dataset_title": ds.title_ua if ds else None,
        "runs": [run_out(r) for r in last],
    }


@router.get("/connectors")
def list_connectors(_: User = Depends(admin_only), db: Session = Depends(get_db)) -> list[dict]:
    """UC-20 кроки 1–2: стан конекторів і останні запуски."""
    return [connector_out(db, c) for c in db.scalars(select(Connector).order_by(Connector.name))]


@router.get("/connectors/{connector_id}/runs")
def connector_runs(
    connector_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Журнал запусків конектора (FR-17)."""
    if db.get(Connector, connector_id) is None:
        raise ApiError(404, "NOT_FOUND")
    rows = db.scalars(
        select(ImportRun)
        .where(ImportRun.connector_id == connector_id)
        .order_by(ImportRun.started_at.desc())
        .limit(limit)
    )
    return [run_out(r) for r in rows]


class ConnectorPatch(BaseModel):
    schedule: str | None = Field(default=None, max_length=100)
    is_enabled: bool | None = None


@router.patch("/connectors/{connector_id}")
def update_connector(
    connector_id: uuid.UUID,
    body: ConnectorPatch,
    request: Request,
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> dict:
    """UC-20 кроки 3–5: розклад (cron) і увімкнення; некоректний розклад — 422, нічого не змінюється (EX1)."""
    c = db.get(Connector, connector_id)
    if c is None:
        raise ApiError(404, "NOT_FOUND")
    patch = body.model_dump(exclude_none=True)
    if "schedule" in patch:
        try:
            patch["schedule"] = schedule.validate(patch["schedule"])
        except schedule.ScheduleError as exc:
            raise ApiError(422, "INVALID_SCHEDULE", reason=str(exc)) from exc
    changes = apply(c, patch)
    log_change(db, request, admin, "connector", c.name, changes)
    db.commit()
    return connector_out(db, c)


def _manual_run(connector_id: uuid.UUID, rid: str) -> None:
    with new_session() as db:
        c = db.get(Connector, connector_id)
        if c is not None:
            service.run_connector(db, c, RunTrigger.MANUAL, rid)


@router.post("/connectors/{connector_id}/run", status_code=202)
def run_connector_now(
    connector_id: uuid.UUID,
    request: Request,
    background: BackgroundTasks,
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> dict:
    """UC-20 AC1 → UC-14: ручний запуск у фоні (запит до API джерела довший за тайм-аут проксі)."""
    c = db.get(Connector, connector_id)
    if c is None:
        raise ApiError(404, "NOT_FOUND")
    rid = request_id(request)
    log_change(db, request, admin, "connector", c.name, {"manual_run": [None, "API"]})
    db.commit()
    background.add_task(_manual_run, c.id, rid)
    return {"message": "Запуск розпочато; результат з'явиться в журналі запусків.", "request_id": rid}


@router.post("/connectors/{connector_id}/upload")
async def upload_official_csv(
    connector_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> dict:
    """UC-20 AC4: резервний імпорт з офіційного CSV-експорту джерела — файл зберігається з SHA-256 (90 днів),
    далі ті самі кроки UC-14: гармонізація, перевірки якості, нова версія лише за зміни значень."""
    c = db.get(Connector, connector_id)
    if c is None:
        raise ApiError(404, "NOT_FOUND")
    raw = await file.read(CSV_MAX + 1)
    if len(raw) > CSV_MAX:
        raise ApiError(413, "FILE_TOO_LARGE")
    spec = sources.SPECS[c.name.split()[-1]]
    countries = sorted(db.scalars(select(Country.code).where(Country.is_active)))
    try:
        result = sources.parse_official_csv(
            spec, raw, countries, date.today().year - 1, file.filename or "upload.csv"
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise ApiError(422, "UNSUPPORTED_CSV", reason=str(exc)[:300]) from exc
    if not result.rows:
        raise ApiError(422, "UNSUPPORTED_CSV", reason="У файлі немає значень для країн платформи")
    log_change(db, request, admin, "connector", c.name, {"manual_run": [None, f"CSV {file.filename}"]})
    db.commit()
    run = service.run_connector(db, c, RunTrigger.MANUAL, request_id(request), result=result)
    return run_out(run)


# ---------------------------------------------------------------------------- країни (FR-43, UC-20 AC2)
ISO3 = "^[A-Z]{3}$"


class CountryIn(BaseModel):
    code: str = Field(pattern=ISO3)
    name_ua: str = Field(min_length=2, max_length=100)
    name_en: str = Field(min_length=2, max_length=100)
    iso2: str | None = Field(default=None, pattern="^[A-Z]{2}$")
    iso_numeric: str | None = Field(default=None, pattern="^[0-9]{3}$")


class CountryPatch(BaseModel):
    name_ua: str | None = Field(default=None, min_length=2, max_length=100)
    name_en: str | None = Field(default=None, min_length=2, max_length=100)
    iso2: str | None = Field(default=None, pattern="^[A-Z]{2}$")
    iso_numeric: str | None = Field(default=None, pattern="^[0-9]{3}$")
    is_active: bool | None = None


def country_out(c: Country) -> dict:
    return {
        "code": c.code,
        "name_ua": c.name_ua,
        "name_en": c.name_en,
        "iso2": c.iso2,
        "iso_numeric": c.iso_numeric,
        "is_active": c.is_active,
    }


@router.post("/countries", status_code=201)
def create_country(
    body: CountryIn, request: Request, admin: User = Depends(admin_only), db: Session = Depends(get_db)
) -> dict:
    """UC-20 AC2: нова країна; після наступного імпорту вона з'являється на карті зеленою."""
    if db.get(Country, body.code) is not None:
        raise ApiError(409, "ALREADY_EXISTS")
    c = Country(**body.model_dump(), is_active=True)
    db.add(c)
    log_change(db, request, admin, "country", c.code, {"created": [None, body.model_dump()]})
    db.commit()
    return country_out(c)


@router.patch("/countries/{code}")
def update_country(
    code: str,
    body: CountryPatch,
    request: Request,
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> dict:
    """Назви, коди, активність. Неактивна країна не імпортується; її наявні дані лишаються (D-05)."""
    c = db.get(Country, code.upper())
    if c is None:
        raise ApiError(404, "NOT_FOUND")
    changes = apply(c, body.model_dump(exclude_none=True))
    log_change(db, request, admin, "country", c.code, changes)
    db.commit()
    return country_out(c)


# ------------------------------------------------------------------------ показники (FR-43, UC-20 AC3, EX2)
class IndicatorFields(BaseModel):
    name_ua: str | None = Field(default=None, min_length=2, max_length=200)
    name_en: str | None = Field(default=None, min_length=2, max_length=200)
    definition: str | None = Field(default=None, min_length=5, max_length=2000)
    unit: str | None = Field(default=None, min_length=1, max_length=40)
    scale: str | None = Field(default=None, min_length=1, max_length=40)
    frequency: Frequency | None = None
    hard_min: float | None = None
    hard_max: float | None = None
    typical_min: float | None = None
    typical_max: float | None = None


class IndicatorIn(IndicatorFields):
    code: str = Field(pattern="^[A-Z][A-Z0-9_]{1,39}$")
    name_ua: str = Field(min_length=2, max_length=200)
    name_en: str = Field(min_length=2, max_length=200)
    definition: str = Field(min_length=5, max_length=2000)
    unit: str = Field(min_length=1, max_length=40)
    scale: str = Field(min_length=1, max_length=40)
    frequency: Frequency


class IndicatorPatch(IndicatorFields):
    is_active: bool | None = None


def indicator_out(i: Indicator) -> dict:
    return {
        "code": i.code,
        "name_ua": i.name_ua,
        "name_en": i.name_en,
        "definition": i.definition,
        "unit": i.unit,
        "scale": i.scale,
        "frequency": i.frequency.value,
        "is_active": i.is_active,
        "hard_min": plain(i.hard_min),
        "hard_max": plain(i.hard_max),
        "typical_min": plain(i.typical_min),
        "typical_max": plain(i.typical_max),
    }


def check_ranges(i: Indicator) -> None:
    """Q-06 / Q-07: можливий діапазон охоплює типовий."""
    lo, hi = plain(i.hard_min), plain(i.hard_max)
    tlo, thi = plain(i.typical_min), plain(i.typical_max)
    pairs = [(lo, hi), (tlo, thi), (lo, tlo), (thi, hi)]
    if any(a is not None and b is not None and a > b for a, b in pairs):
        raise ApiError(422, "INVALID_RANGES")


@router.get("/indicators")
def list_indicators(_: User = Depends(admin_only), db: Session = Depends(get_db)) -> list[dict]:
    """Усі показники, зокрема неактивні (публічний /indicators показує лише активні)."""
    return [indicator_out(i) for i in db.scalars(select(Indicator).order_by(Indicator.code))]


@router.post("/indicators", status_code=201)
def create_indicator(
    body: IndicatorIn, request: Request, admin: User = Depends(admin_only), db: Session = Depends(get_db)
) -> dict:
    """Новий вид даних: після цього постачальники можуть завантажувати його за шаблоном (файл 3)."""
    if db.get(Indicator, body.code) is not None:
        raise ApiError(409, "ALREADY_EXISTS")
    i = Indicator(**body.model_dump(), is_active=True)
    check_ranges(i)
    db.add(i)
    log_change(db, request, admin, "indicator", i.code, {"created": [None, indicator_out(i)]})
    db.commit()
    return indicator_out(i)


@router.patch("/indicators/{code}")
def update_indicator(
    code: str,
    body: IndicatorPatch,
    request: Request,
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> dict:
    i = db.get(Indicator, code.upper())
    if i is None:
        raise ApiError(404, "NOT_FOUND")
    patch = body.model_dump(exclude_none=True)
    if "frequency" in patch and patch["frequency"] != i.frequency and has_data(db, i.code):
        raise ApiError(409, "INDICATOR_HAS_DATA", reason="частоту показника з даними змінити не можна")
    changes = apply(i, patch)
    check_ranges(i)
    log_change(db, request, admin, "indicator", i.code, changes)
    db.commit()
    return indicator_out(i)


def has_data(db: Session, code: str) -> bool:
    return db.scalar(select(Observation.id).where(Observation.indicator_code == code).limit(1)) is not None


@router.delete("/indicators/{code}", status_code=204)
def delete_indicator(
    code: str, request: Request, admin: User = Depends(admin_only), db: Session = Depends(get_db)
) -> None:
    """UC-20 EX2: показник, для якого є дані, видалити не можна — лише деактивувати."""
    i = db.get(Indicator, code.upper())
    if i is None:
        raise ApiError(404, "NOT_FOUND")
    if has_data(db, i.code) or db.scalar(select(Connector.id).where(Connector.name.endswith(f" {i.code}"))):
        raise ApiError(409, "INDICATOR_HAS_DATA")
    snapshot = indicator_out(i)
    db.delete(i)
    log_change(db, request, admin, "indicator", code.upper(), {"deleted": [snapshot, None]})
    db.commit()


# ------------------------------------------------------------------------ категорії закритих даних (FR-43)
class CategoryIn(BaseModel):
    code: str = Field(pattern="^[A-Z][A-Z0-9_]{1,19}$")
    name_ua: str = Field(min_length=2, max_length=100)
    name_en: str = Field(min_length=2, max_length=100)


class CategoryPatch(BaseModel):
    name_ua: str | None = Field(default=None, min_length=2, max_length=100)
    name_en: str | None = Field(default=None, min_length=2, max_length=100)


@router.post("/categories", status_code=201)
def create_category(
    body: CategoryIn, request: Request, admin: User = Depends(admin_only), db: Session = Depends(get_db)
) -> dict:
    if db.get(DataCategory, body.code) is not None:
        raise ApiError(409, "ALREADY_EXISTS")
    c = DataCategory(**body.model_dump())
    db.add(c)
    log_change(db, request, admin, "category", c.code, {"created": [None, body.model_dump()]})
    db.commit()
    return body.model_dump()


@router.patch("/categories/{code}")
def update_category(
    code: str,
    body: CategoryPatch,
    request: Request,
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> dict:
    c = db.get(DataCategory, code.upper())
    if c is None:
        raise ApiError(404, "NOT_FOUND")
    changes = apply(c, body.model_dump(exclude_none=True))
    log_change(db, request, admin, "category", c.code, changes)
    db.commit()
    return {"code": c.code, "name_ua": c.name_ua, "name_en": c.name_en}
