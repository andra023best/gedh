"""Довідники для форм (контракт: x-priority Should, потрібні сценаріям Must). Без значень показників."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serializers import org_public
from app.core.deps import get_db
from app.models.enums import VerificationStatus
from app.models.identity import Organization
from app.models.reference import Country, DataCategory, Indicator

router = APIRouter(tags=["reference"])


@router.get("/organizations")
def verified_organizations(db: Session = Depends(get_db)) -> list[dict]:
    q = (
        select(Organization)
        .where(Organization.status == VerificationStatus.VERIFIED)
        .order_by(Organization.name)
    )
    return [org_public(o) for o in db.scalars(q)]


@router.get("/countries")
def countries(db: Session = Depends(get_db)) -> list[dict]:
    return [
        {
            "code": c.code,
            "name_ua": c.name_ua,
            "name_en": c.name_en,
            "is_active": c.is_active,
            "iso2": c.iso2,
            "iso_numeric": c.iso_numeric,  # S-37: контур на карті
        }
        for c in db.scalars(select(Country).order_by(Country.name_ua))
    ]


@router.get("/categories")
def categories(db: Session = Depends(get_db)) -> list[dict]:
    return [
        {"code": c.code, "name_ua": c.name_ua, "name_en": c.name_en}
        for c in db.scalars(select(DataCategory).order_by(DataCategory.code))
    ]


@router.get("/indicators")
def indicators(db: Session = Depends(get_db)) -> list[dict]:
    return [
        {
            "code": i.code,
            "name_ua": i.name_ua,
            "name_en": i.name_en,
            "definition": i.definition,
            "unit": i.unit,
            "scale": i.scale,
            "frequency": i.frequency.value,
        }
        for i in db.scalars(select(Indicator).where(Indicator.is_active).order_by(Indicator.code))
    ]
