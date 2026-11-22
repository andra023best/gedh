"""Надсилання листів (Brevo HTTP API). Листи — лише сповіщення, без значень закритих даних (DG-25).

Без BREVO_API_KEY лист не надсилається, а записується в журнал сервера й у список OUTBOX
(локальна розробка й тести читають звідти посилання підтвердження).
"""

import json
import logging
import urllib.request
from dataclasses import dataclass

from app.core.config import get_settings

log = logging.getLogger("gedh.mail")
BREVO_URL = "https://api.brevo.com/v3/smtp/email"


@dataclass
class Mail:
    to: str
    subject: str
    text: str


OUTBOX: list[Mail] = []


def send(mail: Mail) -> bool:
    """Надсилає лист; помилку пошти не прокидаємо — основна дія вже збережена."""
    s = get_settings()
    if not s.brevo_api_key:
        OUTBOX.append(mail)
        log.info("лист не надіслано (немає BREVO_API_KEY): тема «%s»", mail.subject)
        return False
    body = {
        "sender": {"email": s.mail_from, "name": s.mail_from_name},
        "to": [{"email": mail.to}],
        "subject": mail.subject,
        "textContent": mail.text,
    }
    req = urllib.request.Request(  # noqa: S310 — адреса фіксована, https
        BREVO_URL,
        data=json.dumps(body).encode(),
        headers={
            "api-key": s.brevo_api_key,
            "content-type": "application/json",
            "accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return 200 <= resp.status < 300
    except Exception as exc:  # noqa: BLE001
        log.error("помилка надсилання листа: %s", type(exc).__name__)
        return False


def page(path: str) -> str:
    return f"{get_settings().frontend_base_url.rstrip('/')}{path}"


def link(path: str, token: str) -> str:
    return f"{page(path)}?token={token}"
