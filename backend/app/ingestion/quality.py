"""Data Quality Engine (UC-18, FR-22, табл. 3.31): правила Q-02 – Q-10 для списку спостережень.

Q-01 (JSON Schema файлу) і Q-11 (першоджерело) стосуються завантаження файлів — етап Е3.
Оцінка DQ-score: 100 × (записи без порушень + 0,5 × записи лише з попередженнями) / усі записи.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from app.models.enums import Frequency, Severity


@dataclass(frozen=True)
class Row:
    country: str
    indicator: str
    period_start: date
    period_end: date
    value: float | None
    breakdown: str | None = None
    note: str | None = None

    @property
    def key(self) -> tuple:
        return (self.country, self.indicator, self.breakdown, self.period_start, self.period_end)

    def ref(self) -> str:
        part = f"/{self.breakdown}" if self.breakdown else ""
        return f"{self.country}/{self.indicator}{part}/{self.period_start.isoformat()}"


@dataclass
class IndicatorRules:
    code: str
    frequency: Frequency
    hard_min: float | None = None
    hard_max: float | None = None
    typical_min: float | None = None
    typical_max: float | None = None


@dataclass
class Issue:
    rule: str
    severity: Severity
    ref: str
    message: str


@dataclass
class Report:
    total: int
    issues: list[Issue] = field(default_factory=list)

    @property
    def critical(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == Severity.CRITICAL]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def has_critical(self) -> bool:
        return bool(self.critical)

    @property
    def score(self) -> float:
        if self.total == 0:
            return 0.0
        bad = min(len({i.ref for i in self.critical}), self.total)
        warned = min(len({i.ref for i in self.warnings} - {i.ref for i in self.critical}), self.total - bad)
        # межі 0–100 гарантовані: порушень не може бути більше, ніж записів (CHECK score_range у БД)
        return round(100 * (self.total - bad - 0.5 * warned) / self.total, 1)


def period_matches(freq: Frequency, start: date, end: date) -> bool:
    """Q-04: період відповідає частоті показника (рік, квартал, місяць)."""
    if start.day != 1 or start > end:
        return False
    months = (end.year - start.year) * 12 + end.month - start.month + 1
    expected = {Frequency.ANNUAL: 12, Frequency.QUARTERLY: 3, Frequency.MONTHLY: 1}[freq]
    end_is_month_end = date.fromordinal(end.toordinal() + 1).day == 1
    quarter_ok = freq != Frequency.QUARTERLY or start.month in (1, 4, 7, 10)
    year_ok = freq != Frequency.ANNUAL or start.month == 1
    return months == expected and end_is_month_end and quarter_ok and year_ok


def evaluate(
    rows: list[Row],
    indicators: dict[str, IndicatorRules],
    countries: set[str],
    previous: dict[tuple, float | None] | None = None,
    today: date | None = None,
) -> Report:
    today = today or date.today()
    report = Report(total=len(rows))
    add = report.issues.append
    seen: set[tuple] = set()
    by_series: dict[tuple, list[Row]] = defaultdict(list)

    for r in rows:
        ref = r.ref()
        # Q-02 повнота: країна, показник, період; значення або позначка відсутності (note)
        if not r.country or not r.indicator or r.period_start is None or (r.value is None and not r.note):
            add(Issue("Q-02", Severity.CRITICAL, ref, "Не заповнено обов'язкові поля"))
            continue
        # Q-03 узгодженість з довідниками
        rules = indicators.get(r.indicator)
        if r.country not in countries or rules is None:
            add(Issue("Q-03", Severity.CRITICAL, ref, "Код країни або показника відсутній у довіднику"))
            continue
        # Q-04 формат періоду й частота
        if not period_matches(rules.frequency, r.period_start, r.period_end):
            add(Issue("Q-04", Severity.CRITICAL, ref, "Період не відповідає частоті показника"))
        # Q-05 унікальність у межах версії
        if r.key in seen:
            add(Issue("Q-05", Severity.CRITICAL, ref, "Повтор комбінації країна–показник–розріз–період"))
        seen.add(r.key)
        by_series[(r.country, r.indicator, r.breakdown)].append(r)
        if r.value is None:
            continue
        # Q-06 фізично можливий діапазон (критично), Q-07 типовий діапазон (попередження)
        if (rules.hard_min is not None and r.value < rules.hard_min) or (
            rules.hard_max is not None and r.value > rules.hard_max
        ):
            add(Issue("Q-06", Severity.CRITICAL, ref, f"Значення {r.value} поза фізично можливим діапазоном"))
        elif (rules.typical_min is not None and r.value < rules.typical_min) or (
            rules.typical_max is not None and r.value > rules.typical_max
        ):
            add(Issue("Q-07", Severity.WARNING, ref, f"Значення {r.value} поза типовим діапазоном"))
        # Q-08 відхилення від попередньої версії > 20 % (лише ненульові попередні значення)
        if previous:
            old = previous.get(r.key)
            if old not in (None, 0) and abs(r.value - old) / abs(old) > 0.20:
                add(Issue("Q-08", Severity.WARNING, ref, f"Перегляд значення: було {old}, стало {r.value}"))

    for (country, indicator, _), series in by_series.items():
        rules = indicators[indicator]
        step = {Frequency.ANNUAL: 12, Frequency.QUARTERLY: 3, Frequency.MONTHLY: 1}[rules.frequency]
        starts = sorted(r.period_start for r in series)
        span = (starts[-1].year - starts[0].year) * 12 + starts[-1].month - starts[0].month
        expected = span // step + 1
        missing = expected - len([r for r in series if r.value is not None])
        ref = f"{country}/{indicator}"
        # Q-09 пропуски не більше 30 % періоду
        if expected > 0 and missing / expected > 0.30:
            add(Issue("Q-09", Severity.WARNING, ref, f"Пропущено {missing} з {expected} періодів"))
        # Q-10 актуальність: останнє значення не старше двох періодів частоти
        last = max(r.period_end for r in series if r.value is not None) if missing < expected else None
        months_old = (today.year - last.year) * 12 + today.month - last.month if last else None
        if months_old is not None and months_old > 2 * step:
            add(Issue("Q-10", Severity.WARNING, ref, f"Останнє значення за період до {last.isoformat()}"))
    return report
