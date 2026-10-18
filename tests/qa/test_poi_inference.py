"""POI / landmark and abstention behaviour (§4 items 10 and 13).

POI positive inference requires the W2 `poi` seed + `lookup_poi`; until it is integrated those tests
SKIP by design (no gold is fabricated from nothing). Abstention rules are contract-level and run today.
"""
from __future__ import annotations

import inspect

import pytest

from .helpers import LOW_CONFIDENCE, parse_one

POI_ONLY = ["магазин Береке", "аптека Дәрмен-Плюс", "ТЦ Северное Сияние", "магазин Шынар", "Казпочта"]


@pytest.mark.parametrize("raw", POI_ONLY)
def test_poi_only_string_yields_landmark_only_and_low_confidence(raw):
    """A bare POI must not invent a precise address (§4-10 negative: не выдумывать city/street/house)."""
    r = parse_one(raw)
    assert r["landmark"] is not None, r
    assert r["province"] is None and r["district"] is None and r["living_area"] is None, r
    assert r["street_type"] is None and r["street_name"] is None and r["house"] is None, r
    assert r["apartment"] is None and r["entrance"] is None and r["floor"] is None, r
    assert r["confidence"] <= LOW_CONFIDENCE, r


def test_poi_marker_word_is_kept_in_landmark_verbatim():
    for marker in ("магазин", "аптека", "ТЦ", "трц", "Казпочта", "Qazpost"):
        r = parse_one(f"{marker} Тестовый")
        assert r["landmark"] and r["landmark"].startswith(marker), (marker, r)


@pytest.mark.parametrize("raw,street,house", [
    ("Бейбарыс 41 магазин Жулдыз", "Бейбарыс", "41"),
    ("ул. Фурманова 20 аптека Здоровье", "Фурманова", "20"),
    ("магазин Мерей, Павлодар, ул. Торайгырова 62", "Торайгырова", "62"),
])
def test_poi_alongside_a_real_address_fills_both_without_confusion(raw, street, house):
    r = parse_one(raw)
    assert r["street_name"] == street and r["house"] == house, r
    assert r["landmark"] is not None, r


def test_duplicate_poi_names_in_different_cities_do_not_leak_into_each_other():
    a = parse_one("Алматы, магазин Дуэт")
    b = parse_one("Караганда, магазин Дуэт")
    for r in (a, b):
        assert r["city"] is None or r["city"] in ("Алматы", "Караганда"), r
        assert r["house"] is None and r["street_name"] is None, f"POI must not fabricate a precise address: {r}"


# ----------------------------------------------------------------------------- §4-13 abstention
def test_identical_street_names_in_different_cities_abstain_without_city():
    """«Мира» exists in many cities; without a city in the text the parser must not pick one."""
    r = parse_one("ул. Мира 17, кв 21")
    assert r["city"] is None and r["province"] is None and r["district"] is None, r


def test_district_shared_by_several_cities_abstains(synthetic_geo):
    """Two fictitious cities both have a «Центральный» district → city/province must stay null."""
    r = parse_one("Центральный район, ул. Тестовая 5")
    assert r["district"] == "Центральный", r
    assert r["city"] is None and r["province"] is None, f"ambiguous district was resolved by guessing: {r}"


def test_city_mention_wins_over_an_ambiguous_district(synthetic_geo):
    r = parse_one("Староград, Центральный район, ул. Тестовая 5")
    assert r["city"] == "Староград" and r["province"] == "Староградская область", r


def test_unique_district_resolves_to_its_city(synthetic_geo):
    r = parse_one("Северный район, ул. Тестовая 5")
    assert r["city"] == "Новогорск" and r["province"] == "Новогорская область", r


def test_microdistrict_name_shared_by_cities_abstains(synthetic_geo):
    """resolve_area must return None when two cities know a micro-district with the same name."""
    import app.geo as geo
    assert geo.resolve_area("Тестовый-1", None) is None
    assert geo.resolve_area("Тестовый-1", "Новогорск") is not None
    r = parse_one("мкр Тестовый-1, д 4")
    assert r["city"] is None and r["district"] is None, f"ambiguous micro-district was resolved by guessing: {r}"
    assert r["street_name"] == "Тестовый-1", r


def _geo_has(name: str) -> bool:
    import app.geo as geo
    return callable(getattr(geo, name, None))


requires_lookup_poi = pytest.mark.skipif(not _geo_has("lookup_poi"),
                                         reason="W2 not integrated yet: app.geo.lookup_poi отсутствует; "
                                                "POI-обогащение тестируется, как только seed `poi` будет подключён")


@requires_lookup_poi
def test_lookup_poi_contract_signature():
    import app.geo as geo
    params = list(inspect.signature(geo.lookup_poi).parameters)
    assert "name" in params and ("city" in params or params == ["name"]), params


def _candidates(res) -> list:
    """lookup_poi contract (W2): list of candidates; unique == exactly 1; None/[] == nothing. Accept dict for a single hit."""
    if res is None:
        return []
    return [res] if isinstance(res, dict) else list(res)


@requires_lookup_poi
@pytest.mark.defect("POI-AMBIG")
def test_ambiguous_poi_name_without_city_is_never_resolved_to_one_branch():
    """«Береке» has branches in several cities (synthetic seed of W2): a bare name must not give exactly one candidate."""
    import app.geo as geo
    cands = _candidates(geo.lookup_poi("Береке"))
    assert len(cands) != 1, f"ambiguous name resolved to a single branch: {cands!r}"


@requires_lookup_poi
@pytest.mark.defect("POI-AMBIG")
def test_missing_poi_gives_no_candidates():
    import app.geo as geo
    assert _candidates(geo.lookup_poi("НесуществующийМагазин-QA-7731")) == []


@requires_lookup_poi
@pytest.mark.defect("POI-AMBIG")
def test_ambiguous_poi_mention_does_not_fill_city_street_or_house():
    r = parse_one("магазин Береке")
    assert r["city"] is None and r["street_name"] is None and r["house"] is None and r["province"] is None, r


@requires_lookup_poi
@pytest.mark.pending
@pytest.mark.skip(reason="adjudication-pending: контракт не фиксирует, может ли уникальный POI заполнять "
                         "city/street/house из linked_address; решение за владельцем контракта")
def test_unique_poi_enrichment_policy_pending():
    raise AssertionError("never executed")
