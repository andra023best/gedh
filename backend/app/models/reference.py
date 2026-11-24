"""Пакет referenceData — довідники (рис. 5.4 звіту)."""

from sqlalchemy import Boolean, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, enum_col
from app.models.enums import Frequency


class Country(Base):
    __tablename__ = "countries"

    code: Mapped[str] = mapped_column(String(3), primary_key=True)  # ISO 3166-1 alpha-3
    name_ua: Mapped[str] = mapped_column(String(100))
    name_en: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # S-37: коди для конекторів і карти (UC-20 AC2: нова країна стає «зеленою» після імпорту)
    iso2: Mapped[str | None] = mapped_column(String(2))  # BIS використовує alpha-2
    iso_numeric: Mapped[str | None] = mapped_column(String(3))  # контури карти (Natural Earth)


class Indicator(Base):
    __tablename__ = "indicators"

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name_ua: Mapped[str] = mapped_column(String(200))
    name_en: Mapped[str] = mapped_column(String(200))
    definition: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(40))
    scale: Mapped[str] = mapped_column(String(40))
    frequency: Mapped[Frequency] = enum_col(Frequency)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Пороги правил якості Q-06 (фізично можливий діапазон) і Q-07 (типовий діапазон) — нотатки, розд. 2.
    hard_min: Mapped[float | None] = mapped_column(Numeric)
    hard_max: Mapped[float | None] = mapped_column(Numeric)
    typical_min: Mapped[float | None] = mapped_column(Numeric)
    typical_max: Mapped[float | None] = mapped_column(Numeric)


class DataCategory(Base):
    __tablename__ = "data_categories"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name_ua: Mapped[str] = mapped_column(String(100))
    name_en: Mapped[str] = mapped_column(String(100))
