"""Заповнення БД: `python -m app.seed` — довідники; `python -m app.seed --demo` — ще й демо-дані.

Скрипт ідемпотентний: повторний запуск не створює дублікатів (стабільні UUID через uuid5).
"""

import argparse
import calendar
import hashlib
import random
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import consent, security
from app.core.config import get_settings
from app.core.db import new_session
from app.ingestion.sources import BASE_URLS, SPECS
from app.models.datasets import DatasetVersion, Observation, OpenDataset, RestrictedDataset, SourceReference
from app.models.enums import (
    AccountStatus,
    Classification,
    ConsentAction,
    DatasetStatus,
    Frequency,
    VerificationStatus,
    VersionStatus,
)
from app.models.identity import ConsentRecord, Organization, User
from app.models.ingestion import Connector, QualityReport, QualityRule
from app.models.reference import Country, DataCategory, Indicator
from app.seed import demo_data, reference_data

NS = uuid.UUID("6f1c3a52-8d2e-4f7b-9a10-5c2b7e9d4e01")  # простір імен GEDH для uuid5
UNUSABLE_PASSWORD = "!disabled"  # не є хешем Argon2 — вхід неможливий, доки Е2 не задасть пароль
METHODOLOGY = "Згенеровано детермінованим генератором app/seed; не є офіційною статистикою (D-04)"
LICENCES = {"WB": "CC BY 4.0", "IMF": "IMF Terms of Use", "BIS": "BIS Terms of Use"}


def sid(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(NS, f"{kind}:{key}")


def set_demo_passwords(s: Session, users: list[User]) -> None:
    """Пароль демо-користувачів — лише зі змінної DEMO_PASSWORD (не в коді); плюс запис згоди."""
    password = get_settings().demo_password
    if password and not security.password_problems(password):
        for u in users:
            if not security.verify_password(u.password_hash, password):
                u.password_hash = security.hash_password(password)
    for u in users:
        has_consent = s.scalar(select(ConsentRecord.id).where(ConsentRecord.user_id == u.id))
        if not has_consent:
            s.add(
                ConsentRecord(user_id=u.id, text_version=consent.CURRENT_VERSION, action=ConsentAction.GIVEN)
            )


def seed_reference(s: Session) -> None:
    for code, ua, en in reference_data.COUNTRIES:
        s.merge(
            Country(
                code=code,
                name_ua=ua,
                name_en=en,
                is_active=True,
                iso2=reference_data.ISO2.get(code),
                iso_numeric=reference_data.ISO_NUMERIC.get(code),
            )
        )
    for row in reference_data.OPEN_INDICATORS:
        code, ua, en, definition, unit, scale, freq, hmin, hmax, tmin, tmax, _ = row
        s.merge(
            Indicator(
                code=code,
                name_ua=ua,
                name_en=en,
                definition=definition,
                unit=unit,
                scale=scale,
                frequency=freq,
                is_active=True,
                hard_min=hmin,
                hard_max=hmax,
                typical_min=tmin,
                typical_max=tmax,
            )
        )
    for (
        code,
        ua,
        en,
        definition,
        unit,
        scale,
        freq,
        hmin,
        hmax,
        tmin,
        tmax,
    ) in reference_data.RESTRICTED_INDICATORS:
        s.merge(
            Indicator(
                code=code,
                name_ua=ua,
                name_en=en,
                definition=definition,
                unit=unit,
                scale=scale,
                frequency=freq,
                is_active=True,
                hard_min=hmin,
                hard_max=hmax,
                typical_min=tmin,
                typical_max=tmax,
            )
        )
    for code, ua, en in reference_data.CATEGORIES:
        s.merge(DataCategory(code=code, name_ua=ua, name_en=en))
    for code, dim, desc, sev in reference_data.QUALITY_RULES:
        s.merge(QualityRule(code=code, dimension=dim, description=desc, severity=sev, is_active=True))
    # Відкриті набори: по одному на показник; дані — імпортом з API джерел (етап Е2+, S-14)
    for code, ua, en, *_, src in reference_data.OPEN_INDICATORS:
        source = reference_data.SOURCE_NAMES[src]
        s.merge(
            OpenDataset(
                id=sid("open", code),
                title_ua=f"{ua} ({source})",
                title_en=f"{en} ({source})",
                classification=Classification.OPEN,
                licence=LICENCES[src],
                is_synthetic=False,
                source_system=src,
            )
        )
    s.flush()
    # Конектор на кожен відкритий набір (Connector → target 1); назва «ДЖЕРЕЛО КОД_ПОКАЗНИКА»
    for code, spec in SPECS.items():
        s.merge(
            Connector(
                id=sid("connector", code),
                name=f"{spec.source} {code}",
                source_system=spec.source,
                base_url=f"{BASE_URLS[spec.source]} ({spec.series})",
                schedule="0 3 * * 1",  # щотижня, понеділок 03:00 UTC (концепція §4.4)
                is_enabled=True,
                target_dataset_id=sid("open", code),
            )
        )


def periods(freq: Frequency, years: tuple[int, int]) -> list[tuple[date, date]]:
    out = []
    for y in range(years[0], years[1] + 1):
        if freq == Frequency.ANNUAL:
            out.append((date(y, 1, 1), date(y, 12, 31)))
        elif freq == Frequency.QUARTERLY:
            for q in range(4):
                m = 3 * q + 3
                out.append((date(y, m - 2, 1), date(y, m, calendar.monthrange(y, m)[1])))
        else:
            for m in range(1, 13):
                out.append((date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])))
    return out


def synthetic_rows(
    key: str,
    indicator: str,
    freq: Frequency,
    breakdowns: list[str],
    years: tuple[int, int],
    base: float,
    spread: float,
) -> list[tuple[str, date, date, float]]:
    """Детермінований генератор: однаковий ключ → однакові значення на будь-якому комп'ютері."""
    rng = random.Random(key)
    rows = []
    for b in breakdowns:
        factor = 1 + rng.uniform(-spread, spread)
        for t, (start, end) in enumerate(periods(freq, years)):
            if indicator == "R_CPI":  # індекс до попереднього місяця коливається біля бази
                value = base + rng.gauss(0, spread)
            else:  # рівень із трендом 4 % за період і шумом 3 %
                value = (
                    base
                    * factor
                    * (
                        1
                        + 0.04
                        * t
                        / (12 if freq == Frequency.MONTHLY else 4 if freq == Frequency.QUARTERLY else 1)
                    )
                )
                value *= 1 + rng.gauss(0, 0.03)
            rows.append((b, start, end, round(value, 1)))
    return rows


def seed_demo(s: Session) -> None:
    now = datetime.now(UTC)
    orgs = {}
    for key, name, otype, country in demo_data.ORGANIZATIONS:
        orgs[key] = s.merge(
            Organization(
                id=sid("org", key),
                name=name,
                type=otype,
                country_code=country,
                status=VerificationStatus.VERIFIED,
                verified_at=now,
            )
        )
    users = {}
    for email, name, position, country, role, org_key in demo_data.USERS:
        existing = s.get(User, sid("user", email))
        users[email] = existing or s.merge(
            User(
                id=sid("user", email),
                full_name=name,
                email=email,
                password_hash=UNUSABLE_PASSWORD,
                position=position,
                country_code=country,
                role=role,
                status=AccountStatus.ACTIVE,
                organization_id=orgs[org_key].id if org_key else None,
            )
        )
    s.flush()
    set_demo_passwords(s, list(users.values()))

    providers = {org_key: sid("user", email) for email, *_, role, org_key in demo_data.USERS if org_key}
    freq_of = {code: freq for code, _, _, _, _, _, freq, *_ in reference_data.RESTRICTED_INDICATORS}
    for (
        key,
        ua,
        en,
        country,
        category,
        indicator,
        owner,
        breakdowns,
        years,
        base,
        spread,
    ) in demo_data.RESTRICTED_DATASETS:
        ds_id = sid("dataset", key)
        s.merge(
            RestrictedDataset(
                id=ds_id,
                title_ua=f"{ua} (синтетичні)",
                title_en=f"{en} (synthetic)",
                classification=Classification.RESTRICTED,
                licence=demo_data.SYNTHETIC_LICENCE,
                is_synthetic=True,
                status=DatasetStatus.ACTIVE,
                country_code=country,
                category_code=category,
                owner_org_id=orgs[owner].id,
            )
        )
        ver_id = sid("version", f"{key}:1")
        if s.get(DatasetVersion, ver_id):
            continue
        rows = synthetic_rows(key, indicator, freq_of[indicator], breakdowns, years, base, spread)
        canonical = "\n".join(f"{country},{indicator},{b},{a},{z},{v}" for b, a, z, v in rows)
        s.add(
            DatasetVersion(
                id=ver_id,
                dataset_id=ds_id,
                version_no=1,
                status=VersionStatus.PUBLISHED,
                file_hash=hashlib.sha256(canonical.encode()).hexdigest(),
                published_at=now,
                uploaded_by_id=providers[owner],
                published_by_id=sid("user", "dg.officer@demo.gedh.invalid"),
            )
        )
        s.flush()
        s.add(
            SourceReference(
                version_id=ver_id,
                document_title="Синтетичний набір для демонстрації GEDH (демо)",
                internal_ref=f"DEMO-{key.upper()}",
                methodology=METHODOLOGY,
                responsible_person=next(
                    n for e, n, *_ in demo_data.USERS if sid("user", e) == providers[owner]
                ),
            )
        )
        s.add_all(
            Observation(
                version_id=ver_id,
                country_code=country,
                indicator_code=indicator,
                breakdown=b,
                period_start=a,
                period_end=z,
                value=v,
            )
            for b, a, z, v in rows
        )
        s.add(
            QualityReport(
                version_id=ver_id, records_total=len(rows), critical_count=0, warning_count=0, score=100.0
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Заповнення БД GEDH")
    parser.add_argument("--demo", action="store_true", help="також демо-організації, користувачі й набори")
    args = parser.parse_args()
    with new_session() as s, s.begin():
        seed_reference(s)
        if args.demo:
            s.flush()
            seed_demo(s)
    with new_session() as s:
        counts = {
            m.__tablename__: len(s.scalars(select(m)).all())
            for m in (Country, Indicator, DataCategory, QualityRule, Organization, User)
        }
        counts["datasets"] = len(s.scalars(select(OpenDataset.id)).all()) + len(
            s.scalars(select(RestrictedDataset.id)).all()
        )
        counts["observations"] = len(s.scalars(select(Observation.id)).all())
    print("seed ok:", counts)


if __name__ == "__main__":
    main()
