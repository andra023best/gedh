"""Контракт API docs/openapi.yaml (етап Е1б, S-13): валідний OpenAPI 3.1 і покриває всі вимоги Must."""

import re
from pathlib import Path

import pytest
import yaml
from openapi_spec_validator import validate

CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "openapi.yaml"
METHODS = {"get", "post", "put", "patch", "delete"}
# Вимоги Must з табл. 3.1 звіту (D-43, D-46)
MUST_FR = {
    "FR-01", "FR-02", "FR-03", "FR-04", "FR-05", "FR-11", "FR-18", "FR-19", "FR-20", "FR-21", "FR-22",
    "FR-23", "FR-24", "FR-25", "FR-26", "FR-27", "FR-28", "FR-30", "FR-31", "FR-34", "FR-42",
}  # fmt: skip
ROLES = {"GUEST", "SYSTEM", "ANALYST", "ORG_REPRESENTATIVE", "DATA_PROVIDER", "ADMIN", "DG_OFFICER"}
VALID_KEY = re.compile(r"^[A-Za-z0-9_$.+\-/{}]+$")


@pytest.fixture(scope="module")
def spec() -> dict:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


def operations(spec):
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if method in METHODS:
                yield path, method, op


def test_contract_is_valid_openapi(spec):
    validate(spec)


def test_no_text_split_into_keys(spec):
    """Неекранована кома чи двокрапка в YAML перетворює частину тексту на ключ — ловимо це."""
    bad = []

    def walk(node, where):
        if isinstance(node, dict):
            for k, v in node.items():
                if (
                    isinstance(k, str)
                    and not VALID_KEY.match(k)
                    and not where.endswith(("/properties", "/headers"))
                ):
                    bad.append(f"{where}: {k!r}")
                walk(v, f"{where}/{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{where}[{i}]")

    walk(spec, "")
    assert not bad, bad


def test_every_operation_has_roles_and_unique_id(spec):
    ids = []
    for path, method, op in operations(spec):
        assert op.get("x-roles"), f"{method} {path}: немає x-roles"
        assert set(op["x-roles"]) <= ROLES, f"{method} {path}: невідома роль"
        ids.append(op["operationId"])
    assert len(ids) == len(set(ids))


def test_all_must_requirements_are_covered(spec):
    covered = {fr for _, _, op in operations(spec) for fr in op.get("x-fr", [])}
    # FR-30 (журнал аудиту) реалізується наскрізно — через x-audit у кожній значущій операції
    audited = [op for _, _, op in operations(spec) if op.get("x-audit")]
    assert audited
    covered.add("FR-30")
    assert MUST_FR <= covered, sorted(MUST_FR - covered)


def test_guest_operations_are_public(spec):
    for path, method, op in operations(spec):
        if op["x-roles"] == ["GUEST"]:
            assert op.get("security") == [], f"{method} {path}: гостьова операція вимагає автентифікації"


def test_all_paths_under_api_prefix_servers(spec):
    assert all(s["url"].endswith("/api") for s in spec["servers"])


def test_every_route_is_in_contract_and_back(spec):
    """S-38: маршрути FastAPI ↔ docs/openapi.yaml в обидва боки (шлях і метод), щоб контракт не відставав."""
    import importlib
    import pkgutil
    import re

    from fastapi.routing import APIRoute

    import app.api as api_pkg

    norm = lambda p: re.sub(r"\{[^}]+\}", "{}", p)  # noqa: E731
    in_contract = {
        (m.upper(), norm(path))
        for path, item in spec["paths"].items()
        for m in item
        if m in ("get", "post", "put", "patch", "delete")
    }
    in_code = set()
    for mod in pkgutil.iter_modules(api_pkg.__path__):
        router = getattr(importlib.import_module(f"app.api.{mod.name}"), "router", None)
        if router is None or mod.name == "diag":  # діагностика Е0 — поза контрактом, вимикається (T-02)
            continue
        for r in router.routes:
            if isinstance(r, APIRoute):
                in_code |= {
                    (m, norm(r.path)) for m in r.methods if m in ("GET", "POST", "PUT", "PATCH", "DELETE")
                }
    assert not in_code - in_contract, f"немає в контракті: {sorted(in_code - in_contract)}"
    assert not in_contract - in_code, f"немає в коді: {sorted(in_contract - in_code)}"
