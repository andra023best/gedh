"""Спільні налаштування тестів: ліміт запитів вимкнено (окремий тест вмикає його сам), листи — в OUTBOX."""

import os

os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ["SCHEDULER_ENABLED"] = "false"  # S-40: фоновий планувальник у тестах не запускаємо
os.environ.pop("BREVO_API_KEY", None)
os.environ.pop("GEDH_PROXY_SECRET", None)

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()
