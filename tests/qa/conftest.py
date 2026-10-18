"""pytest wiring for tests/qa: import path, markers, shared fixtures."""
from __future__ import annotations

import copy
import sys
from typing import Any

import pytest

from .helpers import ROOT

sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure(config: pytest.Config) -> None:
    for line in (
        "defect(id): test pins a defect class tracked in W5_REPORT.md (plain FAIL on baseline is intended RED status)",
        "pending: adjudication-pending / feature not integrated yet; skipped by design, no gold fabricated",
        "soft: expectation derived by QA from the task text/analogy rather than quoted from the contract",
        "rest: needs a real uvicorn subprocess on localhost (skips with a reason if it cannot start)",
    ):
        config.addinivalue_line("markers", line)


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


# --- synthetic geography injected via monkeypatch (never touches data/gazetteer.json) -------------------
SYNTH_CITIES = [
    {"name": "Новогорск", "province": "Новогорская область", "aliases": ["Новогорск"],
     "districts": ["Центральный", "Северный"], "provenance": "W5-QA synthetic fixture", "synthetic": True},
    {"name": "Староград", "province": "Староградская область", "aliases": ["Староград"],
     "districts": ["Центральный", "Южный"], "provenance": "W5-QA synthetic fixture", "synthetic": True},
]
SYNTH_AREAS = [
    {"name": "Тестовый-1", "aliases": ["Тестовый-1", "Тестовый 1"], "city": "Новогорск", "district": "Северный",
     "provenance": "W5-QA synthetic fixture", "synthetic": True},
    {"name": "Тестовый-1", "aliases": ["Тестовый-1", "Тестовый 1"], "city": "Староград", "district": "Южный",
     "provenance": "W5-QA synthetic fixture", "synthetic": True},
]
SYNTH_POI = [
    {"name": "Береке", "aliases": ["Береке"], "type": "магазин", "city": "Новогорск", "district": None,
     "branch_id": "SYN-B-1", "linked_address": None, "provenance": "W5-QA synthetic fixture", "synthetic": True},
    {"name": "Береке", "aliases": ["Береке"], "type": "магазин", "city": "Староград", "district": None,
     "branch_id": "SYN-B-2", "linked_address": None, "provenance": "W5-QA synthetic fixture", "synthetic": True},
    {"name": "Уникум", "aliases": ["Уникум"], "type": "магазин", "city": "Новогорск", "district": "Северный",
     "branch_id": "SYN-U-1", "linked_address": None, "provenance": "W5-QA synthetic fixture", "synthetic": True},
]


def _clear_caches(*modules) -> None:
    for mod in modules:
        for obj in vars(mod).values():
            clear = getattr(obj, "cache_clear", None)
            if callable(clear):
                clear()


@pytest.fixture
def synthetic_geo(monkeypatch: pytest.MonkeyPatch):
    """Gazetteer = real data + two fictitious cities that share the district «Центральный» and the
    micro-district «Тестовый-1». Replaces get_data() in geo and parser modules for the test only.

    If the geo module builds its own index that bypasses get_data() (possible after W2), the injection
    cannot work; the test is then SKIPPED with a reason instead of failing for the wrong reason."""
    import app.geo as geo
    import app.parser as parser
    data: dict[str, Any] = copy.deepcopy(geo.get_data())
    data["cities"] = [*data["cities"], *copy.deepcopy(SYNTH_CITIES)]
    data["provinces"] = [*data["provinces"], "Новогорская область", "Староградская область"]
    data["living_areas"] = [*data.get("living_areas", []), *copy.deepcopy(SYNTH_AREAS)]
    data["poi"] = copy.deepcopy(SYNTH_POI)
    monkeypatch.setattr(geo, "get_data", lambda: data)
    monkeypatch.setattr(parser, "get_data", lambda: data, raising=False)
    _clear_caches(geo, parser)
    try:
        city, _ = geo.find_city("Староград")
        if not city or city.get("name") != "Староград":
            pytest.skip("geo module does not read get_data() on every call (cached index): synthetic gazetteer "
                        "injection unsupported - adapt tests/qa/conftest.py::synthetic_geo to the new geo API")
        yield data
    finally:
        monkeypatch.undo()
        _clear_caches(geo, parser)
