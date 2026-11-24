"""Конектори до API джерел відкритих даних (UC-14, FR-14, FR-15): запит, сира відповідь, гармонізація.

Кожен конектор повертає FetchResult: адресу запиту, сирі байти відповіді (зберігаються з хешем, 90 днів)
і рядки, приведені до кодів ISO 3166-1 alpha-3, кодів показників GEDH, одиниць і частоти довідника.
Перевірено на живих API 22.11.2026 (S-22).
"""

import csv
import io
import json
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from app.ingestion.quality import Row

USER_AGENT = "GEDH-connector/0.1 (student project)"
FIRST_YEAR = 2000


@dataclass
class FetchResult:
    request_url: str
    raw: bytes
    rows: list[Row]


@dataclass(frozen=True)
class Spec:
    indicator: str
    source: str  # WB | IMF | BIS
    series: str  # код у джерелі
    scale: float = 1.0  # множник гармонізації (напр. USD → млрд USD)


# Відповідність показників GEDH рядам джерел (концепція §4.1)
SPECS: dict[str, Spec] = {
    "GDP_USD": Spec("GDP_USD", "WB", "NY.GDP.MKTP.CD", 1e-9),
    "GDP_GROWTH": Spec("GDP_GROWTH", "WB", "NY.GDP.MKTP.KD.ZG"),
    "GDP_PC": Spec("GDP_PC", "WB", "NY.GDP.PCAP.CD"),
    "CPI_INFL": Spec("CPI_INFL", "WB", "FP.CPI.TOTL.ZG"),
    "UNEMP": Spec("UNEMP", "WB", "SL.UEM.TOTL.ZS"),
    "GOV_DEBT": Spec("GOV_DEBT", "IMF", "GGXWDG_NGDP"),
    "CA_BAL": Spec("CA_BAL", "IMF", "BCA_NGDPD"),
    "TRADE_BAL": Spec("TRADE_BAL", "WB", "NE.RSB.GNFS.ZS"),
    "EXPORTS": Spec("EXPORTS", "WB", "NE.EXP.GNFS.ZS"),
    "POLICY_RATE": Spec("POLICY_RATE", "BIS", "WS_CBPOL"),
}

BASE_URLS = {
    "WB": "https://api.worldbank.org/v2",
    "IMF": "https://www.imf.org/external/datamapper/api/v1",
    "BIS": "https://stats.bis.org/api/v1",
}

# ISO 3166-1 alpha-3 → alpha-2 (BIS використовує alpha-2)
ISO2 = {
    "UKR": "UA",
    "POL": "PL",
    "DEU": "DE",
    "FRA": "FR",
    "ITA": "IT",
    "ESP": "ES",
    "NLD": "NL",
    "SWE": "SE",
    "GBR": "GB",
    "CHE": "CH",
    "TUR": "TR",
    "USA": "US",
    "CAN": "CA",
    "MEX": "MX",
    "BRA": "BR",
    "ARG": "AR",
    "CHN": "CN",
    "JPN": "JP",
    "KOR": "KR",
    "IND": "IN",
    "IDN": "ID",
    "AUS": "AU",
    "ZAF": "ZA",
    "SAU": "SA",
    "EGY": "EG",
}
# Члени єврозони: облікова ставка — ставка ЄЦБ (ряд XM), D-37
EURO_AREA = {"DEU", "FRA", "ITA", "ESP", "NLD"}


def year_period(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 — фіксовані https-адреси джерел
        return resp.read()


def fetch_wb(spec: Spec, countries: list[str], last_year: int, get: Callable[[str], bytes]) -> FetchResult:
    url = (
        f"{BASE_URLS['WB']}/country/{';'.join(countries)}/indicator/{spec.series}"
        f"?format=json&date={FIRST_YEAR}:{last_year}&per_page=5000"
    )
    raw = get(url)
    payload = json.loads(raw)
    if not isinstance(payload, list) or len(payload) < 2:
        raise ValueError(f"WB: неочікувана відповідь: {str(payload)[:200]}")
    rows = []
    for item in payload[1] or []:
        iso3, value = item.get("countryiso3code"), item.get("value")
        if iso3 in countries and value is not None and item.get("date", "").isdigit():
            start, end = year_period(int(item["date"]))
            rows.append(Row(iso3, spec.indicator, start, end, round(value * spec.scale, 4)))
    return FetchResult(url, raw, rows)


def fetch_imf(spec: Spec, countries: list[str], last_year: int, get: Callable[[str], bytes]) -> FetchResult:
    # DataMapper повертає всі країни й прогнози WEO; беремо лише наші країни й роки до last_year
    url = f"{BASE_URLS['IMF']}/{spec.series}/{'/'.join(countries)}"
    raw = get(url)
    series = json.loads(raw)["values"][spec.series]
    rows = []
    for iso3 in countries:
        for year, value in (series.get(iso3) or {}).items():
            if year.isdigit() and FIRST_YEAR <= int(year) <= last_year and value is not None:
                start, end = year_period(int(year))
                note = "оцінка IMF WEO" if int(year) == last_year else None
                rows.append(
                    Row(iso3, spec.indicator, start, end, round(float(value) * spec.scale, 4), note=note)
                )
    return FetchResult(url, raw, rows)


def fetch_bis(spec: Spec, countries: list[str], last_year: int, get: Callable[[str], bytes]) -> FetchResult:
    # Місячні ставки на кінець періоду → річне значення = грудень (концепція §4.1: «місяць → кінець року»)
    areas = sorted({"XM" if c in EURO_AREA else ISO2[c] for c in countries if c in ISO2})
    url = (
        f"{BASE_URLS['BIS']}/data/{spec.series}/M.{'+'.join(areas)}/all"
        f"?startPeriod={FIRST_YEAR}-12&endPeriod={last_year}-12&format=csv&detail=dataonly"
    )
    raw = get(url)
    december: dict[tuple[str, int], float] = {}
    for rec in csv.DictReader(io.StringIO(raw.decode("utf-8"))):
        period, value = rec.get("TIME_PERIOD", ""), rec.get("OBS_VALUE", "")
        if period.endswith("-12") and value not in ("", "NaN"):
            december[(rec["REF_AREA"], int(period[:4]))] = float(value)
    rows = []
    for iso3 in countries:
        area = "XM" if iso3 in EURO_AREA else ISO2.get(iso3)
        note = "ставка ЄЦБ (ряд BIS XM)" if iso3 in EURO_AREA else None
        for (ref_area, year), value in december.items():
            if ref_area == area:
                start, end = year_period(year)
                rows.append(Row(iso3, spec.indicator, start, end, round(value * spec.scale, 4), note=note))
    return FetchResult(url, raw, rows)


FETCHERS = {"WB": fetch_wb, "IMF": fetch_imf, "BIS": fetch_bis}


def fetch(
    spec: Spec, countries: list[str], last_year: int, get: Callable[[str], bytes] = http_get
) -> FetchResult:
    return FETCHERS[spec.source](spec, countries, last_year, get)


def parse_official_csv(
    spec: Spec, raw: bytes, countries: list[str], last_year: int, filename: str = "upload.csv"
) -> FetchResult:
    """UC-20 AC4: резервний імпорт, якщо API джерела тривалий час недоступний.

    Приймає два формати:
    1. **офіційний CSV-експорт World Bank** («API_<ряд>_DS2_…csv»): службові рядки зверху,
       далі заголовок `Country Name, Country Code, Indicator Name, Indicator Code, 1960, …`;
       береться рядок нашого ряду;
    2. **довгий формат** `country,year,value` (ISO3, рік, значення в одиницях джерела) — для експортів
       IMF і BIS, збережених у CSV.
    Далі — та сама гармонізація (масштаб показника), що й для API, і ті самі перевірки якості.
    """
    text = raw.decode("utf-8-sig")
    lines = list(csv.reader(io.StringIO(text)))
    rows: list[Row] = []

    def add(iso3: str, year: int, value: float, note: str | None = None) -> None:
        if iso3 in countries and FIRST_YEAR <= year <= last_year:
            start, end = year_period(year)
            rows.append(Row(iso3, spec.indicator, start, end, round(value * spec.scale, 4), note=note))

    header_at = next((i for i, r in enumerate(lines) if "Country Code" in r and "Indicator Code" in r), None)
    if header_at is not None:
        header = lines[header_at]
        ci, ii = header.index("Country Code"), header.index("Indicator Code")
        years = [(j, int(h)) for j, h in enumerate(header) if h.strip().isdigit()]
        found = False
        for r in lines[header_at + 1 :]:
            if len(r) <= ii or r[ii] != spec.series:
                continue
            found = True
            for j, year in years:
                if j < len(r) and r[j].strip() not in ("", ".."):
                    add(r[ci], year, float(r[j]))
        if not found:
            raise ValueError(f"У файлі немає ряду {spec.series} — це експорт іншого показника")
    else:
        head = [h.strip().lower() for h in (lines[0] if lines else [])]
        if not {"country", "year", "value"} <= set(head):
            raise ValueError("Невідомий формат: потрібен експорт World Bank або колонки country,year,value")
        c, y, v = head.index("country"), head.index("year"), head.index("value")
        for r in lines[1:]:
            if len(r) > max(c, y, v) and r[y].strip().isdigit() and r[v].strip() not in ("", "NaN", ".."):
                add(r[c].strip().upper(), int(r[y]), float(r[v]))
    return FetchResult(f"file:{filename}", raw, rows)
