"""Запуск конектора (UC-14): запит із повторними спробами → сира відповідь → гармонізація → DQ → нова версія.

- API недоступний: 3 повторні спроби зі зростаючою паузою, далі запуск FAILED;
  наявні дані не змінюються (UC-14 EX1, NFR-11).
- Критичні порушення якості: нова версія не створюється, запуск REJECTED_BY_DQ (UC-14 EX2).
- Попередження не блокують публікацію відкритих даних, а зберігаються в DQ-звіті (UC-18 AC1, DG-22).
- Дані не змінилися: нова версія не створюється (FR-16: версія лише в разі зміни значень).
- Нова версія — повний знімок; попередня стає SUPERSEDED і не змінюється (D-05); lineage — previous_id.
"""

import gzip
import hashlib
import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit
from app.ingestion import quality, schedule, sources
from app.models.datasets import DatasetVersion, Observation
from app.models.enums import AuditAction, AuditResult, RunStatus, RunTrigger, VersionStatus
from app.models.ingestion import Connector, ImportRun, QualityIssue, QualityReport
from app.models.reference import Country, Indicator
from app.models.technical import RawPayload

log = logging.getLogger("gedh.ingestion")
RAW_KEEP = timedelta(days=90)
RETRY_DELAYS = (2, 4, 8)  # секунди між спробами


def indicator_rules(db: Session) -> dict[str, quality.IndicatorRules]:
    def f(x):
        return float(x) if x is not None else None

    return {
        i.code: quality.IndicatorRules(
            i.code, i.frequency, f(i.hard_min), f(i.hard_max), f(i.typical_min), f(i.typical_max)
        )
        for i in db.scalars(select(Indicator))
    }


def current_version(db: Session, dataset_id: uuid.UUID) -> DatasetVersion | None:
    return db.scalars(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset_id, DatasetVersion.status == VersionStatus.PUBLISHED)
        .order_by(DatasetVersion.version_no.desc())
    ).first()


def snapshot(db: Session, version: DatasetVersion | None) -> dict[tuple, float | None]:
    if version is None:
        return {}
    return {
        (o.country_code, o.indicator_code, o.breakdown, o.period_start, o.period_end): o.value
        for o in db.scalars(select(Observation).where(Observation.version_id == version.id))
    }


def canonical_hash(rows: list[quality.Row]) -> str:
    lines = sorted(
        f"{r.country},{r.indicator},{r.breakdown or ''},{r.period_start},{r.period_end},{r.value}"
        for r in rows
    )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def fetch_with_retries(fn: Callable[[], sources.FetchResult], sleep=time.sleep) -> sources.FetchResult:
    last: Exception | None = None
    for attempt, delay in enumerate((0, *RETRY_DELAYS)):
        if delay:
            sleep(delay)
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — мережа, HTTP, формат
            last = exc
            log.warning("спроба %s невдала: %s", attempt + 1, type(exc).__name__)
    raise RuntimeError(
        f"джерело недоступне після {len(RETRY_DELAYS) + 1} спроб: {type(last).__name__}: {last}"
    )


def run_connector(
    db: Session,
    connector: Connector,
    trigger: RunTrigger,
    request_id: str,
    get: Callable[[str], bytes] = sources.http_get,
    sleep=time.sleep,
    today: date | None = None,
    result: sources.FetchResult | None = None,
) -> ImportRun:
    """Запуск конектора (UC-14).

    `result` — уже розібраний файл резервного імпорту (UC-20 AC4) замість запиту до API.
    """
    today = today or date.today()
    indicator_code = connector.name.split()[-1]  # «WB GDP_USD» → GDP_USD
    spec = sources.SPECS[indicator_code]
    active = db.scalars(select(Country).where(Country.is_active)).all()
    countries = sorted(c.code for c in active)
    # S-37: коди alpha-2 країн, доданих адміністратором, — для BIS
    # (на Render один процес, тож оновлення словника безпечне)
    sources.ISO2.update({c.code: c.iso2 for c in active if c.iso2})
    run = ImportRun(
        connector_id=connector.id,
        trigger=trigger,
        status=RunStatus.RUNNING,
        request_url=result.request_url if result else sources.BASE_URLS[spec.source],
    )
    db.add(run)
    db.commit()  # запуск видно одразу, навіть якщо далі буде помилка

    try:
        if result is None:
            result = fetch_with_retries(lambda: sources.fetch(spec, countries, today.year - 1, get), sleep)
    except Exception as exc:  # noqa: BLE001
        run.status, run.finished_at, run.error_message = RunStatus.FAILED, datetime.now(UTC), str(exc)[:1000]
        audit.append(
            db,
            action=AuditAction.DATASET_PUBLISH,
            result=AuditResult.ERROR,
            object_type="import_run",
            object_id=run.id,
            request_id=request_id,
            details={"source": spec.source, "indicator": indicator_code, "status": "FAILED"},
        )
        db.commit()
        return run

    run.request_url = result.request_url[:1000]
    run.raw_payload_hash = hashlib.sha256(result.raw).hexdigest()
    run.records_count = len(result.rows)
    db.add(
        RawPayload(
            run_id=run.id,
            sha256=run.raw_payload_hash,
            size=len(result.raw),
            body_gzip=gzip.compress(result.raw),
            delete_after=datetime.now(UTC) + RAW_KEEP,
        )
    )

    previous = current_version(db, connector.target_dataset_id)
    old = snapshot(db, previous)
    report = quality.evaluate(result.rows, indicator_rules(db), set(countries), previous=old, today=today)
    new = {r.key: r.value for r in result.rows}

    status = RunStatus.SUCCEEDED
    version = None
    if report.has_critical:
        status = RunStatus.REJECTED_BY_DQ
    elif new != old:
        next_no = (
            db.scalar(
                select(func.max(DatasetVersion.version_no)).where(
                    DatasetVersion.dataset_id == connector.target_dataset_id
                )
            )
            or 0
        ) + 1
        version = DatasetVersion(
            dataset_id=connector.target_dataset_id,
            version_no=next_no,
            status=VersionStatus.PUBLISHED,
            file_hash=canonical_hash(result.rows),
            published_at=datetime.now(UTC),
            previous_id=previous.id if previous else None,
        )
        db.add(version)
        db.flush()
        if previous is not None:
            previous.status = VersionStatus.SUPERSEDED
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
            for r in result.rows
        )
        run.produced_version_id = version.id

    qr = QualityReport(
        version_id=version.id if version else None,
        records_total=report.total,
        critical_count=len(report.critical),
        warning_count=len(report.warnings),
        score=report.score,
    )
    db.add(qr)
    db.flush()
    db.add_all(
        QualityIssue(report_id=qr.id, rule_code=i.rule, record_ref=i.ref[:100], message=i.message)
        for i in report.issues
    )
    run.status, run.finished_at = status, datetime.now(UTC)
    run.warnings_count = len(report.warnings)  # не колонка: лише для зведення адміністратору (DG-22)
    audit.append(
        db,
        action=AuditAction.DATASET_PUBLISH,
        result=AuditResult.SUCCESS if status != RunStatus.REJECTED_BY_DQ else AuditResult.DENIED,
        object_type="dataset_version" if version else "import_run",
        object_id=version.id if version else run.id,
        request_id=request_id,
        details={
            "source": spec.source,
            "indicator": indicator_code,
            "status": status.value,
            "records": report.total,
            "dq_score": report.score,
            "warnings": len(report.warnings),
            "version_no": version.version_no if version else None,
            "raw_sha256": run.raw_payload_hash,
        },
    )
    db.commit()
    log.info("%s: %s, записів %s, DQ %s", connector.name, status.value, report.total, report.score)
    return run


def run_all(
    db: Session, trigger: RunTrigger, request_id: str, only: set[str] | None = None, **kw
) -> list[dict]:
    out = []
    connectors = db.scalars(select(Connector).where(Connector.is_enabled).order_by(Connector.name)).all()
    day = kw.get("today") or date.today()
    for c in connectors:
        if only and c.name.split()[-1] not in only:
            continue
        # S-37: щоденний запуск за розкладом — лише конектори, чий cron припадає на сьогодні (UC-20)
        if trigger == RunTrigger.SCHEDULE and not schedule.due_on(c.schedule, day):
            continue
        run = run_connector(db, c, trigger, request_id, **kw)
        out.append(
            {
                "connector": c.name,
                "status": run.status.value,
                "records": run.records_count,
                "version_id": str(run.produced_version_id) if run.produced_version_id else None,
                "warnings": getattr(run, "warnings_count", 0),
            }
        )
    notify_admins(db, out)
    return out


STATUS_UK = {"FAILED": "джерело недоступне", "REJECTED_BY_DQ": "відхилено перевіркою якості"}


def notify_admins(db: Session, results: list[dict]) -> None:
    """UC-14 EX1, UC-18 AC1, DG-22 (S-39): зведення адміністраторам — невдалі запуски й попередження якості.

    Один лист / сповіщення на запуск імпорту, а не на кожен конектор; якщо все гаразд — нічого.
    """
    problems = [r for r in results if r["status"] != RunStatus.SUCCEEDED.value or r["warnings"]]
    if not problems:
        return
    from app.core import notifier  # локально: notifier тягне пошту, а цей модуль — і для CLI-імпорту
    from app.models.enums import AccountStatus, Role
    from app.models.identity import User

    lines = []
    for r in problems:
        what = STATUS_UK.get(r["status"], "успішно")
        warn = f", попереджень якості: {r['warnings']}" if r["warnings"] else ""
        lines.append(f"• {r['connector']}: {what}{warn}")
    active_admins = select(User).where(User.role == Role.ADMIN, User.status == AccountStatus.ACTIVE)
    notifier.notify(
        db,
        db.scalars(active_admins).all(),
        "GEDH: результати імпорту відкритих даних",
        "Конектори, що потребують уваги (якщо запуск невдалий, наявні дані не змінено):\n" + "\n".join(lines),
        link="/admin/connectors",
        kind="IMPORT_SUMMARY",
    )
