"""Розбір файлу закритого набору за шаблоном (UC-12, FR-21): CSV або JSON → рядки Row + порушення Q-01, Q-04.

Шаблон один для обох форматів: CSV-колонки country, indicator, breakdown, period, value, note;
JSON — {"observations": [{...ті самі поля...}]}. CSV перетворюється на той самий вигляд і перевіряється
тією самою JSON Schema (правило Q-01), тож правила однакові незалежно від формату.
"""

import calendar
import csv
import io
import json
import re
from datetime import date

from jsonschema import Draft202012Validator

from app.ingestion.quality import Issue, Row
from app.models.enums import Severity

CSV_COLUMNS = ["country", "indicator", "breakdown", "period", "value", "note"]
MAX_ISSUES_PER_RULE = 50  # не засмічуємо звіт тисячами однакових помилок

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://gedh.vercel.app/api/templates/upload.schema.json",
    "title": "GEDH — шаблон завантаження закритого набору",
    "type": "object",
    "required": ["observations"],
    "additionalProperties": False,
    "properties": {
        "observations": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["country", "indicator", "period"],
                "additionalProperties": False,
                "properties": {
                    "country": {
                        "type": "string",
                        "pattern": "^[A-Z]{3}$",
                        "description": "ISO 3166-1 alpha-3",
                    },
                    "indicator": {
                        "type": "string",
                        "pattern": "^[A-Z0-9_]{2,40}$",
                        "description": "код довідника",
                    },
                    "breakdown": {"type": ["string", "null"], "maxLength": 100},
                    "period": {
                        "type": "string",
                        "pattern": r"^\d{4}(-Q[1-4]|-(0[1-9]|1[0-2]))?$",
                        "description": "рік 2025, квартал 2025-Q1 або місяць 2025-03 (ISO 8601)",
                    },
                    "value": {"type": ["number", "null"]},
                    "note": {"type": ["string", "null"], "maxLength": 300},
                },
            },
        }
    },
}
_validator = Draft202012Validator(SCHEMA)

CSV_TEMPLATE = (
    "country,indicator,breakdown,period,value,note\n"
    "UKR,R_CPI,Львівська,2025-03,100.8,\n"
    "UKR,R_CPI,Одеська,2025-03,,дані не зібрано\n"
)


class UnreadableFile(Exception):
    """UC-18 EX1: файл не читається — критична помилка без подальших перевірок."""


def parse_period(text: str) -> tuple[date, date]:
    """2025 → рік, 2025-Q1 → квартал, 2025-03 → місяць."""
    if m := re.fullmatch(r"(\d{4})-Q([1-4])", text):
        y, q = int(m[1]), int(m[2])
        end_month = 3 * q
        return date(y, end_month - 2, 1), date(y, end_month, calendar.monthrange(y, end_month)[1])
    if m := re.fullmatch(r"(\d{4})-(\d{2})", text):
        y, mo = int(m[1]), int(m[2])
        return date(y, mo, 1), date(y, mo, calendar.monthrange(y, mo)[1])
    y = int(text)
    return date(y, 1, 1), date(y, 12, 31)


def _to_number(raw: str) -> float | None | str:
    raw = raw.strip().replace(",", ".") if raw else ""
    if raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return raw  # залишаємо рядком — JSON Schema повідомить про тип


def load(data: bytes) -> dict:
    """Байти → документ шаблону. Формат визначається за вмістом, а не за назвою файлу."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UnreadableFile("Файл не в кодуванні UTF-8") from exc
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as exc:
            raise UnreadableFile(f"Некоректний JSON: {exc.msg}, рядок {exc.lineno}") from exc
        return {"observations": doc} if isinstance(doc, list) else doc
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in (reader.fieldnames or [])]
    if header != CSV_COLUMNS:
        raise UnreadableFile(f"Заголовок CSV має бути: {','.join(CSV_COLUMNS)}")
    rows = []
    for rec in reader:
        item = {k.strip(): (v or "").strip() for k, v in rec.items() if k}
        rows.append(
            {
                "country": item["country"],
                "indicator": item["indicator"],
                "breakdown": item["breakdown"] or None,
                "period": item["period"],
                "value": _to_number(item["value"]),
                "note": item["note"] or None,
            }
        )
    return {"observations": rows}


def count_records(data: bytes) -> int:
    """Скільки записів у файлі (разом із тими, що не пройшли схему) — для DQ-score і звіту."""
    try:
        doc = load(data)
    except UnreadableFile:
        return 0
    items = doc.get("observations") if isinstance(doc, dict) else None
    return len(items) if isinstance(items, list) else 0


def parse(data: bytes) -> tuple[list[Row], list[Issue]]:
    """Повертає рядки, придатні для решти правил якості, і порушення Q-01 (схема)."""
    try:
        doc = load(data)
    except UnreadableFile as exc:
        return [], [Issue("Q-01", Severity.CRITICAL, "file", str(exc))]
    issues: list[Issue] = []
    bad_items: set[int] = set()
    for err in sorted(_validator.iter_errors(doc), key=lambda e: list(e.path)):
        path = list(err.path)
        idx = path[1] if len(path) > 1 and isinstance(path[1], int) else None
        if idx is not None:
            bad_items.add(idx)
        if len([i for i in issues if i.rule == "Q-01"]) < MAX_ISSUES_PER_RULE:
            ref = f"row {idx + 1}" if idx is not None else "file"
            field = f" (поле {path[2]})" if len(path) > 2 else ""
            issues.append(
                Issue("Q-01", Severity.CRITICAL, ref, f"Не відповідає шаблону{field}: {err.message}")
            )
    if not isinstance(doc, dict) or not isinstance(doc.get("observations"), list):
        return [], issues or [Issue("Q-01", Severity.CRITICAL, "file", "Немає масиву observations")]
    rows: list[Row] = []
    for i, item in enumerate(doc["observations"]):
        if i in bad_items:
            continue
        start, end = parse_period(item["period"])
        rows.append(
            Row(
                item["country"],
                item["indicator"],
                start,
                end,
                item.get("value"),
                breakdown=item.get("breakdown"),
                note=item.get("note"),
            )
        )
    return rows, issues
