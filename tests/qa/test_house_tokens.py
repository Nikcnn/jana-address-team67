"""house / apartment / entrance / floor token semantics (str-typed, «1/2» != «1», marker forms, conflicts).

Template: «ул. Мира 5, <fragment>» — street and house are fixed, so every case isolates ONE detail form.
Expected values follow the plain meaning of the marker; they are not copied from parser output.
"""
from __future__ import annotations

import random

import pytest

soft = pytest.mark.soft

from .helpers import fragment_violations, parse_one

BASE = "ул. Мира 5"

APARTMENT = ["кв 12", "кв. 12", "кв.12", "квартира 12", "кв №12", "кв-12", "12 кв", "12 кв.", "12 квартира", "flat 12", "apt 12", "kv 12",
             "2-пәтер", "пәтер 2", "2 пәтер"]
ENTRANCE = ["подъезд 3", "под. 3", "подъезд №3", "3 подъезд", pytest.param("3-подъезд", marks=soft), "3подъезд", "3 подьезд", "подьезд 3",
            pytest.param("кіреберіс 3", marks=soft), pytest.param("3 кіреберіс", marks=soft), "entrance 3"]
FLOOR = ["этаж 9", "этаж: 9", "9 этаж", "9-этаж", "9этаж", "эт. 9", "эт 9", "9 эт.", "қабат 9", "9 қабат", pytest.param("9-қабат", marks=soft), "floor 9"]


def _value(fragment: str) -> str:
    import re
    return re.search(r"\d+", fragment).group(0)


@pytest.mark.parametrize("fragment", APARTMENT)
def test_apartment_marker_forms(fragment):
    r = parse_one(f"{BASE}, {fragment}")
    assert r["apartment"] == _value(fragment), r
    assert r["house"] == "5" and r["street_name"] == "Мира", f"detail leaked into street/house: {r}"
    assert r["entrance"] is None and r["floor"] is None, r


@pytest.mark.parametrize("fragment", ENTRANCE)
def test_entrance_marker_forms(fragment):
    r = parse_one(f"{BASE}, {fragment}")
    assert r["entrance"] == _value(fragment), r
    assert r["house"] == "5" and r["street_name"] == "Мира", r
    assert r["floor"] is None and r["apartment"] is None, f"entrance confused with another detail: {r}"


@pytest.mark.parametrize("fragment", FLOOR)
def test_floor_marker_forms(fragment):
    r = parse_one(f"{BASE}, {fragment}")
    assert r["floor"] == _value(fragment), r
    assert r["house"] == "5" and r["street_name"] == "Мира", r
    assert r["entrance"] is None and r["apartment"] is None, f"floor confused with another detail: {r}"


@pytest.mark.parametrize("tail,house", [
    ("5", "5"), ("13б", "13б"), ("13Б", "13Б"), ("247а", "247а"), ("89/1", "89/1"), ("100/2", "100/2"), pytest.param("12/А", "12/А", marks=soft),
    ("007", "007"), ("д. 15", "15"), ("д 15", "15"), ("дом 15", "15"), ("дом №15", "15"), ("д.15/2", "15/2"), ("дом 15А", "15А"),
    ("15 дом", "15"), ("15дом", "15"), ("үй 15", "15"), ("15 үй", "15"),
])
def test_house_forms_keep_case_letter_and_fraction_as_string(tail, house):
    r = parse_one(f"ул. Абая {tail}")
    assert r["house"] == house and type(r["house"]) is str, r
    assert r["apartment"] is None, f"house part leaked into apartment: {r}"
    assert r["street_name"] == "Абая", r


@pytest.mark.parametrize("house", ["89/1", "100/2", "14/2", "8/3", "1/2"])
def test_slash_after_house_is_never_an_apartment(house):
    r = parse_one(f"ул. Абая {house}, кв 4")
    assert r["house"] == house and r["apartment"] == "4", r


@pytest.mark.parametrize("house", ["89/1", "100/2", "14/2"])
def test_slash_house_without_any_other_detail_leaves_apartment_null(house):
    r = parse_one(f"проспект Абая {house}")
    assert r["house"] == house and r["apartment"] is None and r["entrance"] is None and r["floor"] is None, r


def test_three_details_in_any_order_are_not_confused():
    base = ["кв 4", "подъезд 2", "этаж 3"]
    for perm in ([0, 1, 2], [2, 1, 0], [1, 0, 2], [1, 2, 0]):
        r = parse_one("ул. Мира д 3, " + ", ".join(base[i] for i in perm))
        assert (r["house"], r["apartment"], r["entrance"], r["floor"]) == ("3", "4", "2", "3"), (perm, r)


def test_marker_without_dot_house_marker_d_is_a_house():
    """`д 3` (no dot) is a house marker."""
    r = parse_one("г Семей ул Мира д 3")
    assert r["house"] == "3" and "д 3" not in (r["street_name"] or ""), r


@pytest.mark.parametrize("raw", ["ул. Мира 5 кв 4 кв 6", "ул. Мира 5 подъезд 1 подъезд 2", "ул. Мира 5 этаж 3 этаж 4"])
def test_conflicting_values_for_one_field_give_null_and_lower_confidence(raw):
    """W1 task §4: a conflict between two different values of one field is null + reduced confidence, not a guess."""
    r = parse_one(raw)
    field = "apartment" if "кв" in raw else "entrance" if "подъезд" in raw else "floor"
    assert r[field] is None, f"conflicting {field} values must not be resolved by guessing: {r}"
    clean = parse_one(raw.rsplit(" ", 2)[0])
    assert r["confidence"] < clean["confidence"] + 1e-9 or r["confidence"] <= 0.9, (r["confidence"], clean["confidence"])


def test_same_value_repeated_is_not_a_conflict():
    assert parse_one("ул. Мира 5, кв 4, кв. 4")["apartment"] == "4"


@pytest.mark.parametrize("n", range(1, 400, 41))
@pytest.mark.parametrize("suffix", ["", "а", "Б", "/2"])
def test_house_and_apartment_follow_the_input_numbers_no_memorisation(n, suffix):
    """A parser keyed on whole strings would not follow varying digits."""
    m = (n * 7) % 300 + 1
    r = parse_one(f"ул. Мира {n}{suffix}, кв {m}")
    assert r["house"] == f"{n}{suffix}" and r["apartment"] == str(m), r


def test_values_are_fragments_of_the_input_for_random_compositions():
    rng = random.Random(20261017)
    streets = ["Мира", "Абая", "Толе би", "Сейфуллина", "Желтоксан"]
    for _ in range(150):
        raw = (f"{rng.choice(['ул.', 'улица', 'ул', 'пр.', 'проспект'])} {rng.choice(streets)} "
               f"{rng.randint(1, 300)}{rng.choice(['', 'а', 'Б', '/1', '/2'])}"
               + rng.choice(["", ", кв %d" % rng.randint(1, 99), ", подъезд %d" % rng.randint(1, 9),
                             ", этаж %d" % rng.randint(1, 25), " кв.%d" % rng.randint(1, 99)]))
        assert not fragment_violations(raw, parse_one(raw)), raw
