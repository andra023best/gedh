"""Ручний імпорт відкритих даних.

`python -m app.ingestion` — усі конектори; `python -m app.ingestion GDP_USD` — один показник.
"""

import json
import sys
import uuid

from app.core.db import new_session
from app.ingestion.service import run_all
from app.models.enums import RunTrigger


def main() -> None:
    only = set(sys.argv[1:]) or None
    with new_session() as db:
        result = run_all(db, RunTrigger.MANUAL, request_id=f"cli-{uuid.uuid4()}", only=only)
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
