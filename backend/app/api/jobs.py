"""Службові задачі за розкладом (контракт: tag internal). Викликає GitHub Actions cron із X-Job-Token.

Запит проходить через Vercel, як і решта /api (S-08). Імпорт триває довше, ніж дозволяє проксі,
тому задача запускається у фоні, а відповідь 202 повертається одразу.
"""

import hmac
import uuid

from fastapi import APIRouter, BackgroundTasks, Request

from app.core.config import get_settings
from app.core.db import new_session
from app.core.deps import request_id
from app.core.errors import ApiError
from app.ingestion.service import run_all
from app.models.enums import RunTrigger

router = APIRouter(prefix="/internal/jobs", tags=["internal"])


def check_job_token(request: Request) -> None:
    expected = get_settings().job_token
    sent = request.headers.get("x-job-token", "")
    if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
        raise ApiError(401, "JOB_TOKEN_INVALID")


def _import_job(rid: str, only: set[str] | None) -> None:
    with new_session() as db:
        run_all(db, RunTrigger.SCHEDULE, request_id=rid, only=only)


@router.post("/import", status_code=202)
def import_open_data(request: Request, background: BackgroundTasks, indicator: str | None = None) -> dict:
    """UC-14: запуск усіх увімкнених конекторів (або одного показника)."""
    check_job_token(request)
    rid = request_id(request) or f"job-{uuid.uuid4()}"
    background.add_task(_import_job, rid, {indicator} if indicator else None)
    return {"message": "Імпорт запущено", "request_id": rid}
