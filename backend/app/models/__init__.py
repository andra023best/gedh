"""Моделі SQLAlchemy за доменною моделлю розділу 5.1 звіту (build/class_model.py).

Імпорт усіх модулів тут потрібен, щоб Alembic бачив усі таблиці в Base.metadata.
"""

from app.models import audit, datasets, exchange, identity, ingestion, reference, technical
from app.models.base import Base

__all__ = ["Base", "audit", "datasets", "exchange", "identity", "ingestion", "reference", "technical"]
