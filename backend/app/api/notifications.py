"""Сповіщення в інтерфейсі (FR-29): кожен бачить лише свої. Перегляд сповіщень у журнал аудиту не пишеться —
це не звернення до даних, а службові повідомлення користувачу про його ж заявки й набори."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.serializers import iso
from app.core.deps import current_user, get_db
from app.core.errors import ApiError
from app.models.identity import User
from app.models.technical import Notification

router = APIRouter(prefix="/notifications", tags=["notifications"])


def out(n: Notification) -> dict:
    return {
        "id": str(n.id),
        "kind": n.kind,
        "title": n.title,
        "text": n.text,
        "link": n.link,
        "created_at": iso(n.created_at),
        "read_at": iso(n.read_at),
    }


@router.get("")
def list_notifications(
    unread_only: bool = False,
    limit: int = Query(30, ge=1, le=100),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    q = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    items = db.scalars(q.order_by(Notification.created_at.desc()).limit(limit)).all()
    unread = db.scalar(
        select(func.count()).where(Notification.user_id == user.id, Notification.read_at.is_(None))
    )
    return {"items": [out(n) for n in items], "unread": unread or 0}


@router.post("/{notification_id}/read")
def mark_read(
    notification_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise ApiError(404, "NOT_FOUND")
    if n.read_at is None:
        n.read_at = datetime.now(UTC)
        db.commit()
    return out(n)


@router.post("/read-all")
def mark_all_read(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    n = db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    ).rowcount
    db.commit()
    return {"marked": n}
