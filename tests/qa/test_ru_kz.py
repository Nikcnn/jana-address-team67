"""RU / KZ / Latin / mixed-script behaviour: markers, canonical type mapping, bilingual city, case & letters."""
from __future__ import annotations

import pytest

from .helpers import parse_one, s1

KZ_CASES = [
    # raw, expected subset (strings deliberately differ from data/qa/regression_corpus_v1.json: see ANNOTATION_POLICY §1)
    ("Мұқанов көшесі 31 үй", dict(street_type="улица", street_name="Мұқанов", house="31")),
    ("Тұран даңғылы 22, 8 пәтер", dict(street_type="проспект", street_name="Тұран", house="22", apartment="8")),
    ("Әл-Фараби даңғылы 7/2 үй", dict(street_type="проспект", street_name="Әл-Фараби", house="7/2")),
    ("Алматы қаласы, Төле би көшесі 55, 4 қабат",
     dict(city="Алматы", province="Алматы", street_type="улица", street_name="Төле би", house="55", floor="4")),
    ("Ақтөбе қаласы, Маресьев көшесі 14, 3 қабат",
     dict(city="Актобе", province="Актюбинская область", street_type="улица", street_name="Маресьев", house="14", floor="3")),
    ("Астана қаласы, Сарайшық даңғылы, 21 үй, 15 пәтер",
     dict(city="Астана", province="Астана", street_type="проспект", street_name="Сарайшық", house="21", apartment="15")),
    ("Қарағанды қаласы, Гоголь көшесі 8, 31 пәтер",
     dict(city="Караганда", province="Карагандинская область", street_type="улица", street_name="Гоголь", house="8", apartment="31")),
    ("Орал қаласы, Евразия даңғылы 85/2", dict(city="Уральск", street_type="проспект", street_name="Евразия", house="85/2")),
]


@pytest.mark.parametrize("raw,want", KZ_CASES, ids=[c[0][:40] for c in KZ_CASES])
def test_kz_markers(raw, want):
    r = parse_one(raw)
    got = {k: r[k] for k in want}
    assert got == want, f"\n raw={raw!r}\n got={ {k: v for k, v in r.items() if v is not None and k not in ('id', 'raw_address')} }"


@pytest.mark.parametrize("suffix", ["көшесі", "даңғылы"])
def test_kz_street_type_word_is_not_part_of_street_name(suffix):
    r = parse_one(f"Тестовая {suffix} 5")
    assert r["street_name"] is None or suffix not in r["street_name"], r


@pytest.mark.parametrize("raw,city,province", [
    ("Алматы, ул. Абая 5", "Алматы", "Алматы"), ("Алматы қаласы, ул. Абая 5", "Алматы", "Алматы"),
    ("Almaty, Abay st 5", "Алматы", "Алматы"), ("город Алматы/Алматы қаласы, ул. Абая 5", "Алматы", "Алматы"),
    ("г. Астана, ул. Абая 5", "Астана", "Астана"), ("Нур-Султан, ул. Абая 5", "Астана", "Астана"),
    ("Ақтөбе қаласы, ул. Абая 5", "Актобе", "Актюбинская область"), ("Aktobe, ул. Абая 5", "Актобе", "Актюбинская область"),
])
def test_city_spellings_map_to_one_canonical_city_and_province(raw, city, province):
    r = parse_one(raw)
    assert (r["city"], r["province"]) == (city, province), r


def test_bilingual_slash_city_is_one_city_and_slash_is_not_house():
    r = parse_one("город Актобе/Ақтөбе қаласы, ул Жансугурова 7")
    assert r["city"] == "Актобе" and "/" not in (r["city"] or "")
    assert r["house"] == "7" and "/" not in r["house"]
    assert r["street_name"] == "Жансугурова"


def test_province_name_is_not_mistaken_for_the_city_of_similar_name():
    """«Алматы» must not match inside «Алматинская область» (substring trap)."""
    r = parse_one("Алматинская область, ул. Абая 5")
    assert r["province"] == "Алматинская область"
    assert r["city"] is None, f"city invented from a province name: {r}"


@pytest.mark.parametrize("raw", ["Алматинская область, ул. Абая 5", "Карагандинская область, ул. Абая 5"])
def test_oblast_text_never_creates_a_city(raw):
    assert parse_one(raw)["city"] is None


def test_case_of_house_letter_is_preserved():
    assert parse_one("Алматы қаласы, Абай көшесі 13Б үй")["house"] == "13Б"
    assert parse_one("Алматы қаласы, Абай көшесі 13б үй")["house"] == "13б"
    assert parse_one("ул. Абая 13Б")["house"] == "13Б" and parse_one("ул. Абая 13б")["house"] == "13б"


@pytest.mark.parametrize("raw", ["улица казибек би 5", "улица Казибек Би 5", "улица КАЗИБЕК БИ 5", "улица казыбек би 5"])
def test_street_name_keeps_the_users_spelling_and_case(raw):
    r = parse_one(raw)
    expect = raw.removeprefix("улица ").rsplit(" ", 1)[0]
    assert r["street_name"] == expect, f"street_name must be the user's fragment, got {r['street_name']!r}"


def test_user_spelling_beats_any_canonical_form():
    a, b = parse_one("улица Сатпаева 5")["street_name"], parse_one("улица Сатпаев 5")["street_name"]
    assert (a, b) == ("Сатпаева", "Сатпаев")


@pytest.mark.parametrize("raw,name,house,apt", [
    ("Abay st 5, kv 2", "Abay", "5", "2"),
    pytest.param("Abay street 5, flat 2", "Abay", "5", "2", marks=pytest.mark.soft),
    pytest.param("Abay st 5, apt 2", "Abay", "5", "2", marks=pytest.mark.soft),
])
def test_latin_street_apartment_markers(raw, name, house, apt):
    r = parse_one(raw)
    assert (r["street_name"], r["house"], r["apartment"]) == (name, house, apt), r


@pytest.mark.parametrize("raw", ["ул. Мира 5 кв 3", "УЛ. МИРА 5 КВ 3", "Ул. мира 5 Кв 3", "ул. Мира 5 КВ. 3"])
def test_marker_case_insensitivity_does_not_change_components(raw):
    base = parse_one("ул. Мира 5 кв 3")
    r = parse_one(raw)
    for f in ("street_type", "house", "apartment"):
        assert s1(r[f]) == s1(base[f]), (raw, f, r[f], base[f])
    assert s1(r["street_name"]) == "мира"


def test_microdistrict_reversed_and_prefixed_forms_agree():
    a = parse_one("Нурсити 2 микрорайон 11дом 5 подьезд 239 кв")
    assert (a["street_type"], a["street_name"], a["house"], a["entrance"], a["apartment"]) == ("микрорайон", "Нурсити 2", "11", "5", "239")


@pytest.mark.parametrize("mix,house", [("ул. Абая 5", "5"), ("Абай көшесі 5", "5"), ("проспект Абая 5 үй", "5"), ("Абая көшесі 5 дом", "5")])
def test_ru_and_kz_markers_may_be_mixed_in_one_string(mix, house):
    r = parse_one(mix)
    assert r["house"] == house and r["street_name"] and "көше" not in r["street_name"] and "проспект" not in r["street_name"], r


@pytest.mark.defect("STREET-IS-CITY")
@pytest.mark.parametrize("raw,street", [("Абай көшесі 12 үй", "Абай"), ("Абай даңғылы 3", "Абай"), ("Сәтбаев көшесі 8 үй", "Сәтбаев"),
                                         ("Abay st 9, kv 3", "Abay")])
def test_street_named_like_a_city_is_not_promoted_to_a_city(raw, street):
    """«Абай», «Сатпаев/Сәтбаев» are both real (small) cities and very common street names. With a street-type word
    present and no city marker, the text is a street: inventing city/province is a false fill on a core field."""
    r = parse_one(raw)
    assert r["street_name"] == street, r
    assert r["city"] is None and r["province"] is None, f"street promoted to city (fabricated core fields): {r}"
