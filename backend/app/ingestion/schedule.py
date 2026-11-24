"""Розклад конекторів (UC-20 крок 3, EX1): вираз cron із 5 полів «хвилина година день місяць день_тижня».

Імпорт запускає GitHub Actions щодня о 03:00 UTC (S-37); кожен конектор перевіряє, чи припадає
на сьогодні його розклад (поля «день», «місяць», «день тижня»). Хвилина й година лише перевіряються
на коректність — точний час визначає планувальник.
Підтримується: `*`, числа, списки `1,15`, діапазони `1-5`, кроки `*/2`, `1-10/3`.
"""

from datetime import date, timedelta

FIELDS = (
    ("хвилина", 0, 59),
    ("година", 0, 23),
    ("день місяця", 1, 31),
    ("місяць", 1, 12),
    ("день тижня", 0, 7),
)


class ScheduleError(ValueError):
    pass


def _expand(part: str, lo: int, hi: int, name: str) -> set[int]:
    out: set[int] = set()
    for item in part.split(","):
        step = 1
        if "/" in item:
            item, s = item.split("/", 1)
            if not s.isdigit() or int(s) == 0:
                raise ScheduleError(f"{name}: неправильний крок «{s}»")
            step = int(s)
        if item == "*":
            a, b = lo, hi
        elif "-" in item:
            x, y = item.split("-", 1)
            if not (x.isdigit() and y.isdigit()):
                raise ScheduleError(f"{name}: неправильний діапазон «{item}»")
            a, b = int(x), int(y)
        elif item.isdigit():
            a = b = int(item)
        else:
            raise ScheduleError(f"{name}: неправильне значення «{item}»")
        if not (lo <= a <= hi and lo <= b <= hi and a <= b):
            raise ScheduleError(f"{name}: значення поза межами {lo}–{hi}")
        out.update(range(a, b + 1, step))
    return out


def parse(expr: str) -> list[set[int]]:
    parts = expr.split()
    if len(parts) != 5:
        raise ScheduleError("потрібно 5 полів: хвилина година день місяць день_тижня")
    sets = [_expand(p, lo, hi, name) for p, (name, lo, hi) in zip(parts, FIELDS, strict=True)]
    if 7 in sets[4]:  # 7 і 0 — неділя
        sets[4].add(0)
    return sets


def validate(expr: str) -> str:
    """Нормалізований вираз або ScheduleError (UC-20 EX1: зміни не зберігаються)."""
    expr = " ".join(expr.split())
    parse(expr)
    return expr


def due_on(expr: str, day: date) -> bool:
    """Чи має конектор запуститися цього дня.

    Як у cron: якщо обмежено і день місяця, і день тижня — досить збігу одного з них.
    """
    _, _, dom, month, dow = parse(expr)
    if day.month not in month:
        return False
    parts = expr.split()
    cron_dow = (day.weekday() + 1) % 7  # cron: 0 — неділя; Python: 0 — понеділок
    dom_any, dow_any = parts[2] == "*", parts[4] == "*"
    if dom_any and dow_any:
        return True
    if dom_any:
        return cron_dow in dow
    if dow_any:
        return day.day in dom
    return day.day in dom or cron_dow in dow


def next_due(expr: str, after: date) -> date | None:
    """Найближчий день запуску (протягом року) — для сторінки «Конектори»."""
    for i in range(1, 367):
        d = after + timedelta(days=i)
        if due_on(expr, d):
            return d
    return None
