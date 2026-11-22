"""Демо-дані: вигадані організації «(демо)», користувач для кожної ролі, синтетичні закриті набори (D-04).

E-mail демо-користувачів — у зарезервованому домені .invalid (RFC 2606), тож листи нікуди не підуть.
Паролів тут немає: до етапу Е2 вхід неможливий; паролі демо-користувачів задаватимуться змінною середовища.
"""

from app.models.enums import OrganizationType, Role

# (ключ, назва, тип, країна, домен e-mail)
ORGANIZATIONS = [
    ("stat_ukr", "Національне статистичне агентство (демо)", OrganizationType.GOVERNMENT, "UKR"),
    ("trade_pol", "Центр торговельної статистики (демо)", OrganizationType.GOVERNMENT, "POL"),
    ("labour_deu", "Інститут досліджень ринку праці (демо)", OrganizationType.UNIVERSITY, "DEU"),
    ("delta_ukr", "Аналітична агенція «Дельта» (демо)", OrganizationType.ANALYTICS_COMPANY, "UKR"),
]

# (e-mail, ПІБ, посада, країна, роль, ключ організації або None)
USERS = [
    ("admin@demo.gedh.invalid", "Адміністратор Платформи", "адміністратор", "UKR", Role.ADMIN, None),
    ("dg.officer@demo.gedh.invalid", "DG-офіцер Демо", "аудитор", "UKR", Role.DG_OFFICER, None),
    (
        "provider.ukr@demo.gedh.invalid",
        "Постачальник Україна",
        "статистик",
        "UKR",
        Role.DATA_PROVIDER,
        "stat_ukr",
    ),
    (
        "provider.pol@demo.gedh.invalid",
        "Постачальник Польща",
        "статистик",
        "POL",
        Role.DATA_PROVIDER,
        "trade_pol",
    ),
    (
        "provider.deu@demo.gedh.invalid",
        "Постачальник Німеччина",
        "дослідник",
        "DEU",
        Role.DATA_PROVIDER,
        "labour_deu",
    ),
    (
        "rep.delta@demo.gedh.invalid",
        "Представник «Дельти»",
        "аналітик",
        "UKR",
        Role.ORG_REPRESENTATIVE,
        "delta_ukr",
    ),
    ("analyst@demo.gedh.invalid", "Аналітик Демо", None, "POL", Role.ANALYST, None),
]

UA_REGIONS = ["м. Київ", "Львівська", "Харківська", "Одеська", "Дніпропетровська"]
DE_REGIONS = ["Bayern", "Berlin", "Hamburg", "Sachsen"]
PL_PARTNERS = ["DEU", "CZE", "FRA", "GBR", "UKR"]

# Синтетичні закриті набори: (ключ, назва UA, назва EN, країна, категорія, показник, власник,
#   розрізи, періоди (рік_від, рік_до), база значення, розкид)
RESTRICTED_DATASETS = [
    (
        "ukr_prices",
        "Регіональні індекси споживчих цін",
        "Regional consumer price indices",
        "UKR",
        "PRICES",
        "R_CPI",
        "stat_ukr",
        UA_REGIONS,
        (2024, 2025),
        100.9,
        0.8,
    ),
    (
        "ukr_grp",
        "Валовий регіональний продукт",
        "Gross regional product",
        "UKR",
        "REGIONAL",
        "GRP",
        "stat_ukr",
        UA_REGIONS,
        (2019, 2024),
        14.0,
        0.35,
    ),
    (
        "pol_trade",
        "Експорт за країнами-партнерами",
        "Exports by partner country",
        "POL",
        "TRADE",
        "TRADE_PARTNER",
        "trade_pol",
        PL_PARTNERS,
        (2019, 2024),
        30_000.0,
        0.45,
    ),
    (
        "deu_wages",
        "Середня зарплата за федеральними землями",
        "Average wage by federal state",
        "DEU",
        "LABOUR",
        "WAGE_REGION",
        "labour_deu",
        DE_REGIONS,
        (2023, 2025),
        4_300.0,
        0.12,
    ),
]

SYNTHETIC_LICENCE = "GEDH DSA (демо, синтетичні дані)"
