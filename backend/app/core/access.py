"""AccessPolicy — рішення «дозволено / заборонено» для кожного звернення до даних.

UC-16, FR-26; DG-03, DG-05.

RBAC + ABAC (D-03a):
- відкритий набір — будь-який активний обліковий запис (L1);
- закритий набір — DG-офіцер (перевірка й контроль) і постачальник-власник (своя організація) за роллю;
- усі інші — лише за чинним дозволом: той самий набір, статус ACTIVE, `valid_from ≤ зараз < valid_to`
  (строк перевіряється **на кожному зверненні**, навіть якщо щоденна задача
  ще не позначила дозвіл завершеним),
  період дозволу перетинається із запитаним. Видається лише частина, яку покриває дозвіл (UC-16 AC1).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.datasets import Dataset, OpenDataset, RestrictedDataset
from app.models.enums import AccountStatus, GrantStatus, Role
from app.models.exchange import AccessGrant
from app.models.identity import User


@dataclass
class Decision:
    allowed: bool
    reason: str | None = None  # ACCESS_DENIED | GRANT_EXPIRED | GRANT_SUSPENDED
    grant: AccessGrant | None = None
    period: tuple[date, date] | None = None  # межа видачі (None — без обмеження)
    basis: str = ""  # OPEN | ROLE_DG | ROLE_OWNER | GRANT


def authorize(
    db: Session,
    user: User,
    dataset: Dataset,
    period_from: date | None = None,
    period_to: date | None = None,
    now: datetime | None = None,
) -> Decision:
    if user.status != AccountStatus.ACTIVE:
        return Decision(False, "ACCESS_DENIED")
    if isinstance(dataset, OpenDataset):
        return Decision(True, basis="OPEN")
    assert isinstance(dataset, RestrictedDataset)
    if user.role == Role.DG_OFFICER:
        return Decision(True, basis="ROLE_DG")
    if user.role == Role.DATA_PROVIDER and user.organization_id == dataset.owner_org_id:
        return Decision(True, basis="ROLE_OWNER")

    now = now or datetime.now(UTC)
    grants = db.scalars(
        select(AccessGrant).where(AccessGrant.grantee_id == user.id, AccessGrant.dataset_id == dataset.id)
    ).all()
    lo, hi = period_from or date.min, period_to or date.max
    usable = [
        g
        for g in grants
        if g.status == GrantStatus.ACTIVE
        and g.valid_from <= now < g.valid_to
        and g.period_start <= hi
        and g.period_end >= lo
    ]
    if usable:
        g = max(usable, key=lambda x: (x.period_end - x.period_start, x.valid_to))
        return Decision(True, grant=g, period=(max(g.period_start, lo), min(g.period_end, hi)), basis="GRANT")
    # чому відмова — для зрозумілого повідомлення й журналу (UC-16 EX2)
    if any(g.status == GrantStatus.SUSPENDED for g in grants):
        return Decision(False, "GRANT_SUSPENDED")
    if any(g.status == GrantStatus.EXPIRED or g.valid_to <= now for g in grants):
        return Decision(False, "GRANT_EXPIRED")
    return Decision(False, "ACCESS_DENIED")
