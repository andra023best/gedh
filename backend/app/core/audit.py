"""Запис подій у журнал аудиту з хеш-ланцюжком (UC-17, FR-30, DG-17 – DG-19, D-40).

append() викликається в тій самій транзакції, що й основна дія (NFR-12): якщо запис у журнал
не вдався, скасовується все (UC-17 EX1). Рядок AuditTrail (id = 1) блокується SELECT … FOR UPDATE
наприкінці операції, безпосередньо перед COMMIT, тож паралельні події не розгалужують ланцюжок,
а блокування триває мінімально (NFR-02).
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent, AuditTrail
from app.models.enums import AuditAction, AuditResult, Role


def compute_hash(fields: dict, prev_hash: str) -> str:
    canonical = json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((canonical + prev_hash).encode()).hexdigest()


def event_fields(e: AuditEvent) -> dict:
    return {
        "seq": e.seq,
        "occurred_at": e.occurred_at.isoformat(),
        "actor_id": str(e.actor_id) if e.actor_id else None,
        "actor_role": e.actor_role.value if e.actor_role else None,
        "on_behalf_of_id": str(e.on_behalf_of_id) if e.on_behalf_of_id else None,
        "action": e.action.value,
        "object_type": e.object_type,
        "object_id": e.object_id,
        "result": e.result.value,
        "request_id": e.request_id,
        "details": e.details,
    }


def append(
    db: Session,
    *,
    action: AuditAction,
    object_type: str,
    object_id: str | uuid.UUID,
    request_id: str,
    result: AuditResult = AuditResult.SUCCESS,
    actor_id: uuid.UUID | None = None,
    actor_role: Role | None = None,
    on_behalf_of_id: uuid.UUID | None = None,
    details: dict | None = None,
) -> AuditEvent:
    """Додає подію. Деталі — без ПІБ, e-mail, IP і значень закритих даних (табл. 3.26, прим. 1; DG-25)."""
    db.flush()  # спершу основні зміни, потім коротке блокування ланцюжка
    trail = db.scalars(select(AuditTrail).where(AuditTrail.id == 1).with_for_update()).one()
    event = AuditEvent(
        seq=trail.last_seq + 1,
        occurred_at=datetime.now(UTC),
        actor_id=actor_id,
        actor_role=actor_role,
        on_behalf_of_id=on_behalf_of_id,
        action=action,
        object_type=object_type,
        object_id=str(object_id),
        result=result,
        request_id=request_id,
        details=json.dumps(details or {}, sort_keys=True, ensure_ascii=False),
        prev_hash=trail.last_hash,
    )
    event.hash = compute_hash(event_fields(event), trail.last_hash)
    db.add(event)
    trail.last_seq, trail.last_hash = event.seq, event.hash
    db.flush()
    return event


def verify_chain(db: Session) -> int | None:
    """Перераховує ланцюжок; повертає seq першого зламаного запису або None (FR-32, UC-15 AC1)."""
    prev = "0" * 64
    for e in db.scalars(select(AuditEvent).order_by(AuditEvent.seq)):
        if e.prev_hash != prev or compute_hash(event_fields(e), prev) != e.hash:
            return e.seq
        prev = e.hash
    return None
