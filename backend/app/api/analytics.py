"""Аналітика відкритих даних (Е6б): профіль країни (UC-05, FR-08) і картка показника (FR-09).

Порівняння країн і рейтинг (UC-06, FR-10, FR-36) будуються на сторінці з рядів
`GET /datasets/{id}/observations`, окремих маршрутів не потребують.
Карта покриття (UC-04, FR-06) — `GET /coverage` (data.py).
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.data import source_info, summary
from app.api.serializers import iso
from app.core import audit
from app.core.deps import current_user, get_db, request_id
from app.core.errors import ApiError
from app.models.datasets import Dataset, DatasetVersion, Observation, OpenDataset, RestrictedDataset
from app.models.enums import AuditAction, VersionStatus
from app.models.identity import User
from app.models.reference import Country, Indicator

router = APIRouter(tags=["analytics"])


def current_open_versions():
    """Чинні опубліковані версії відкритих наборів (D-05: попередні — SUPERSEDED)."""
    return (
        select(DatasetVersion.id)
        .join(OpenDataset, OpenDataset.id == DatasetVersion.dataset_id)
        .where(DatasetVersion.status == VersionStatus.PUBLISHED)
    )


def indicator_meta(i: Indicator) -> dict:
    return {
        "code": i.code,
        "name_ua": i.name_ua,
        "name_en": i.name_en,
        "definition": i.definition,
        "unit": i.unit,
        "scale": i.scale,
        "frequency": i.frequency.value,
    }


def period_label(start, frequency: str) -> str:
    if frequency == "ANNUAL":
        return str(start.year)
    if frequency == "QUARTERLY":
        return f"{start.year}-Q{(start.month - 1) // 3 + 1}"
    return start.strftime("%Y-%m")


@router.get("/countries/{code}/profile")
def country_profile(
    code: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    """UC-05 (FR-08): для кожного відкритого показника — ряд, останнє значення, зміна до попереднього
    періоду, джерело, дата оновлення й версія; перелік закритих наборів країни без значень (AC2).

    Один агрегований запит до спостережень (NFR-02). Перегляд записується в журнал (постумова UC-05).
    """
    country = db.get(Country, code.upper())
    if country is None:
        raise ApiError(404, "NOT_FOUND")
    rows = db.execute(
        select(Observation, DatasetVersion, Dataset)
        .join(DatasetVersion, DatasetVersion.id == Observation.version_id)
        .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
        .where(Observation.version_id.in_(current_open_versions()), Observation.country_code == country.code)
        .order_by(Observation.indicator_code, Observation.period_start)
    ).all()
    meta = {i.code: i for i in db.scalars(select(Indicator))}
    by_ind: dict[str, dict] = {}
    for o, v, d in rows:
        i = meta[o.indicator_code]
        entry = by_ind.get(o.indicator_code)
        if entry is None:
            src = source_info(db, d, v)
            entry = by_ind[o.indicator_code] = {
                **indicator_meta(i),
                "dataset_id": str(d.id),
                "dataset_title": d.title_ua,
                "version_no": v.version_no,
                "published_at": iso(v.published_at),
                "source": src.get("document_title"),
                "licence": d.licence,
                "series": [],
            }
        entry["series"].append(
            {
                "period": period_label(o.period_start, i.frequency.value),
                "start": iso(o.period_start),
                "value": o.value,
                "note": o.note,
            }
        )
    indicators = []
    for entry in by_ind.values():
        known = [p for p in entry["series"] if p["value"] is not None]
        latest = known[-1] if known else None
        previous = known[-2] if len(known) > 1 else None
        entry["latest"] = latest and {
            "period": latest["period"],
            "value": latest["value"],
            "note": latest["note"],
        }
        entry["previous"] = previous and {"period": previous["period"], "value": previous["value"]}
        entry["change"] = round(latest["value"] - previous["value"], 6) if latest and previous else None
        indicators.append(entry)
    indicators.sort(key=lambda e: e["name_ua"])
    restricted = db.scalars(
        select(RestrictedDataset)
        .where(RestrictedDataset.country_code == country.code)
        .order_by(RestrictedDataset.title_ua)
    ).all()
    audit.append(
        db,
        action=AuditAction.DATA_VIEW,
        object_type="country",
        object_id=country.code,
        request_id=request_id(request),
        actor_id=user.id,
        actor_role=user.role,
        on_behalf_of_id=user.organization_id,
        details={"view": "country_profile", "indicators": len(indicators), "records": len(rows)},
    )
    db.commit()
    return {
        "country": {"code": country.code, "name_ua": country.name_ua, "name_en": country.name_en},
        "indicators": indicators,
        # UC-05 AC2: закриті набори — лише опис і позначка «доступ за заявкою», без значень
        "restricted": [summary(db, d) for d in restricted],
    }


@router.get("/indicators/{code}")
def indicator_card(code: str, db: Session = Depends(get_db)) -> dict:
    """FR-09 (DG-13): метадані показника без значень — доступні й гостю (UC-04 AC1).

    Код, назва, визначення, одиниця, частота, джерело, класифікація, набори, де він є, і покриття країн.
    """
    i = db.get(Indicator, code.upper())
    if i is None:
        raise ApiError(404, "NOT_FOUND")
    current = current_open_versions()
    datasets = []
    for d in db.scalars(select(Dataset).order_by(Dataset.kind, Dataset.title_ua)):
        v = db.scalar(
            select(DatasetVersion.id)
            .where(DatasetVersion.dataset_id == d.id, DatasetVersion.status == VersionStatus.PUBLISHED)
            .order_by(DatasetVersion.version_no.desc())
            .limit(1)
        )
        if v and db.scalar(
            select(Observation.id)
            .where(Observation.version_id == v, Observation.indicator_code == i.code)
            .limit(1)
        ):
            datasets.append(summary(db, d))
    countries = db.execute(
        select(
            Observation.country_code, func.min(Observation.period_start), func.max(Observation.period_start)
        )
        .where(
            Observation.version_id.in_(current),
            Observation.indicator_code == i.code,
            Observation.value.is_not(None),
        )
        .group_by(Observation.country_code)
        .order_by(Observation.country_code)
    ).all()
    open_ds = next((x for x in datasets if x["kind"] == "OPEN"), None)
    return {
        **indicator_meta(i),
        "classification": "OPEN" if open_ds else ("RESTRICTED" if datasets else None),
        "source": (open_ds["source_system"] if open_ds else None),
        "licence": open_ds["licence"] if open_ds else (datasets[0]["licence"] if datasets else None),
        "datasets": datasets,
        "coverage": [
            {
                "country": c,
                "from": period_label(lo, i.frequency.value),
                "to": period_label(hi, i.frequency.value),
            }
            for c, lo, hi in countries
        ],
    }
