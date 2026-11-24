"""Notifier (табл. 5.5): сповіщення в інтерфейсі (FR-29) і той самий текст листом (FR-39).

Викликається ПІСЛЯ фіксації основної дії: сповіщення — наслідок, а не частина транзакції (якщо лист не
пішов, рішення щодо заявки не скасовується). Тексти — без значень закритих даних (DG-25).
Листи про реєстрацію й підтвердження e-mail ідуть лише поштою: користувач ще не може увійти.
"""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail
from app.models.identity import User
from app.models.technical import Notification


def notify(
    db: Session,
    users: Iterable[User | None],
    subject: str,
    text: str,
    *,
    link: str | None = None,
    kind: str = "STATUS",
    ref: str | None = None,
) -> int:
    """Додає сповіщення кожному адресату (без повторів) і надсилає лист. Повертає кількість адресатів."""
    seen: dict = {}
    for u in users:
        if u is not None and u.id not in seen:
            seen[u.id] = u
    for u in seen.values():
        db.add(
            Notification(
                user_id=u.id,
                kind=kind,
                ref=ref,
                title=subject.removeprefix("GEDH: ")[:200],
                text=text,
                link=link,
            )
        )
    db.commit()
    for u in seen.values():
        body = text + (f"\n\nВідкрити: {mail.page(link)}" if link else "")
        mail.send(mail.Mail(to=u.email, subject=subject, text=body))
    return len(seen)


def already_sent(db: Session, user_id, kind: str, ref: str) -> bool:
    """Для нагадувань: таке сповіщення вже було (UC-19 AC2 — одне нагадування на дозвіл)."""
    return (
        db.scalar(
            select(Notification.id).where(
                Notification.user_id == user_id, Notification.kind == kind, Notification.ref == ref
            )
        )
        is not None
    )
