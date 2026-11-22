"""Правила data governance, які гарантує сама БД (міграції 0001, 0002).

Потрібна база з виконаними міграціями й демо-даними: `alembic upgrade head`, `python -m app.seed --demo`.
Кожен тест працює в транзакції, яку наприкінці відкочено, тож база не змінюється.
"""

import os
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_engine
from app.models.datasets import DatasetVersion, Observation, RestrictedDataset
from app.models.enums import (
    AccessRequestStatus,
    ApprovalStage,
    ConsentAction,
    DecisionType,
    GrantStatus,
)
from app.models.exchange import AccessGrant, AccessRequest, ApprovalDecision, DataSharingAgreement
from app.models.identity import ConsentRecord, User
from app.seed.__main__ import sid

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="БД не налаштовано")

REP = sid("user", "rep.delta@demo.gedh.invalid")
ADMIN = sid("user", "admin@demo.gedh.invalid")
DELTA = sid("org", "delta_ukr")
UKR_PRICES = sid("dataset", "ukr_prices")


@pytest.fixture
def s():
    conn = get_engine().connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    trans.rollback()
    conn.close()


def violates(s: Session, fn) -> str:
    """Виконує дію в точці збереження й повертає текст помилки БД (або падає, якщо помилки не було)."""
    with pytest.raises((IntegrityError, DBAPIError)) as exc, s.begin_nested():
        fn()
        s.flush()
    return str(exc.value.orig)


def new_request(s: Session) -> AccessRequest:
    req = AccessRequest(
        number=f"T-{uuid.uuid4().hex[:8]}",
        requester_id=REP,
        on_behalf_of_id=DELTA,
        country_code="UKR",
        category_code="PRICES",
        target_dataset_id=UKR_PRICES,
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        purpose="тест",
        usage_place="Київ",
        requested_term_days=90,
        responsible_person="Тест",
        status=AccessRequestStatus.SUBMITTED,
    )
    s.add(req)
    s.flush()
    return req


def test_seed_loaded(s):
    assert s.scalar(select(text("count(*)")).select_from(text("countries"))) == 25
    assert s.get(RestrictedDataset, UKR_PRICES).is_synthetic


def test_dg02_dataset_class_is_read_only(s):
    msg = violates(
        s,
        lambda: s.execute(
            text("UPDATE datasets SET classification = 'OPEN' WHERE id = :id"), {"id": UKR_PRICES}
        ),
    )
    assert "DG-02" in msg


def test_dg02_restricted_dataset_must_have_owner_and_class(s):
    msg = violates(
        s,
        lambda: s.execute(text("UPDATE datasets SET owner_org_id = NULL WHERE id = :id"), {"id": UKR_PRICES}),
    )
    assert "restricted_fields" in msg


def test_d01_requester_cannot_approve_own_request(s):
    req = new_request(s)
    msg = violates(
        s,
        lambda: s.add(
            ApprovalDecision(
                request_id=req.id,
                stage=ApprovalStage.ADMIN,
                decision=DecisionType.APPROVED,
                decided_by_id=REP,
            )
        ),
    )
    assert "D-01" in msg
    # інший користувач — можна
    s.add(
        ApprovalDecision(
            request_id=req.id, stage=ApprovalStage.ADMIN, decision=DecisionType.APPROVED, decided_by_id=ADMIN
        )
    )
    s.flush()


def test_only_one_decision_per_stage(s):
    req = new_request(s)
    s.add(
        ApprovalDecision(
            request_id=req.id, stage=ApprovalStage.ADMIN, decision=DecisionType.APPROVED, decided_by_id=ADMIN
        )
    )
    s.flush()
    violates(
        s,
        lambda: s.add(
            ApprovalDecision(
                request_id=req.id,
                stage=ApprovalStage.ADMIN,
                decision=DecisionType.REJECTED,
                decided_by_id=ADMIN,
            )
        ),
    )


def test_request_term_max_180_days(s):
    def too_long():
        req = new_request(s)
        req.requested_term_days = 181

    assert "term_max_180" in violates(s, too_long)


def test_grant_validity_max_180_days(s):
    req = new_request(s)
    dsa = DataSharingAgreement(request_id=req.id, text_version="1.0", text_hash="0" * 64)
    s.add(dsa)
    s.flush()
    now = datetime.now(UTC)
    msg = violates(
        s,
        lambda: s.add(
            AccessGrant(
                agreement_id=dsa.id,
                grantee_id=REP,
                dataset_id=UKR_PRICES,
                period_start=date(2024, 1, 1),
                period_end=date(2024, 12, 31),
                valid_from=now,
                valid_to=now + timedelta(days=181),
                status=GrantStatus.ACTIVE,
            )
        ),
    )
    assert "validity_max_180" in msg


def test_dg10_consent_records_are_append_only(s):
    rec = ConsentRecord(user_id=REP, text_version="1.0", action=ConsentAction.GIVEN, ip_address="203.0.113.7")
    s.add(rec)
    s.flush()
    msg = violates(
        s,
        lambda: s.execute(
            text("UPDATE consent_records SET action = 'WITHDRAWN' WHERE id = :id"), {"id": rec.id}
        ),
    )
    assert "DG-10" in msg


def test_observation_unique_within_version_even_without_breakdown(s):
    ver = s.scalar(select(DatasetVersion).where(DatasetVersion.dataset_id == UKR_PRICES))

    def dup():
        for _ in range(2):
            s.add(
                Observation(
                    version_id=ver.id,
                    country_code="UKR",
                    indicator_code="R_CPI",
                    breakdown=None,
                    period_start=date(2030, 1, 1),
                    period_end=date(2030, 1, 31),
                    value=100.0,
                )
            )

    assert "uq_observations_natural_key" in violates(s, dup)


def test_period_start_not_after_end(s):
    ver = s.scalar(select(DatasetVersion).where(DatasetVersion.dataset_id == UKR_PRICES))
    msg = violates(
        s,
        lambda: s.add(
            Observation(
                version_id=ver.id,
                country_code="UKR",
                indicator_code="R_CPI",
                period_start=date(2030, 2, 1),
                period_end=date(2030, 1, 1),
                value=1.0,
            )
        ),
    )
    assert "period_order" in msg


def test_user_email_unique_case_insensitive(s):
    def dup():
        s.add(User(full_name="X", email="ADMIN@demo.gedh.invalid", password_hash="!", country_code="UKR"))

    assert "uq_users_email_lower" in violates(s, dup)
