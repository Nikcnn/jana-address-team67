"""Contract conformance C4/C5/C6/C7 on the Python interface (parse_address / parse_batch).

Everything here follows the frozen contract, not the current implementation.
"""
from __future__ import annotations

import inspect
import logging

import pytest

from .helpers import (ADVERSARIAL_SAFE, COMPONENTS, LOW_CONFIDENCE, OUTPUT_KEYS, STRING_NUMBER_FIELDS, components,
                      corpus_acceptance, corpus_all_safe, corpus_holdout_raws, corpus_repo_data, fragment_violations,
                      parse_one, shape_violations, time_limit)


# ----------------------------------------------------------------------------- C6 frozen interfaces
def test_fields_constant_is_the_11_component_names_in_order():
    from app.parser import FIELDS
    assert isinstance(FIELDS, tuple)
    assert list(FIELDS) == COMPONENTS


def test_public_signatures_are_frozen():
    from app.parser import parse_address, parse_batch
    assert list(inspect.signature(parse_address).parameters) == ["record"]
    assert list(inspect.signature(parse_batch).parameters) == ["records"]


def test_geo_public_functions_keep_signatures():
    import app.geo as geo
    expect = {"get_data": [], "find_province": ["text"], "find_city": ["text"],
              "resolve_area": ["name", "city"], "city_for_district": ["district"]}
    for fn, params in expect.items():
        assert list(inspect.signature(getattr(geo, fn)).parameters) == params, fn


def test_gazetteer_keeps_required_top_level_keys():
    import app.geo as geo
    data = geo.get_data()
    assert {"_meta", "cities", "provinces", "living_areas"} <= set(data)
    assert {"source", "limitations"} <= set(data["_meta"]), "_meta must carry source/limitations (C6)"


# ----------------------------------------------------------------------------- C4 shape on every corpus
@pytest.mark.parametrize("name,loader", [
    ("acceptance", corpus_acceptance), ("regression_corpus_v1", corpus_holdout_raws),
    ("repo_data", corpus_repo_data), ("adversarial_safe", lambda: [r for _, r in ADVERSARIAL_SAFE]),
])
def test_every_output_record_has_exactly_14_typed_fields(name, loader):
    from app.parser import parse_batch
    raws = loader()
    recs = [{"id": f"{name}-{i}", "raw_address": r} for i, r in enumerate(raws)]
    with time_limit(60):
        out = parse_batch(recs)
    assert len(out) == len(recs)
    bad = {i: v for i, o in enumerate(out) if (v := shape_violations(o))}
    assert not bad, f"{len(bad)} nonconformant of {len(out)}; first: " + "; ".join(
        f"[{i}] {raws[i]!r}: {v}" for i, v in list(bad.items())[:3])


def test_house_apartment_entrance_floor_are_strings_never_numbers():
    for raw in ["ул. Мира 5 кв 3 подъезд 2 этаж 7", "ул. Мира 1/2 кв 3", "ул. Мира 007", "Назарбаева8 кв5"]:
        r = parse_one(raw)
        for f in STRING_NUMBER_FIELDS:
            assert r[f] is None or type(r[f]) is str, f"{f} of {raw!r} is {type(r[f]).__name__}"


def test_house_leading_zeros_are_not_coerced_to_numbers():
    assert parse_one("ул. Мира 007")["house"] == "007"
    assert parse_one("ул. Мира 5, кв 007")["apartment"] == "007"


def test_fraction_house_is_not_truncated_to_integer():
    """«1/2» != «1» (C9)."""
    r = parse_one("ул. Абая 1/2")
    assert r["house"] == "1/2" and r["house"] != "1"


def test_unparseable_valid_string_is_full_record_with_nulls_and_low_confidence():
    """Contract C3: a valid but unparsed string is still a 14-field record, never skipped."""
    for raw in ["asdfgh", "zxcvbnm lkjh", "!!!???", "я"]:
        r = parse_one(raw)
        assert not shape_violations(r), (raw, shape_violations(r))
        assert all(r[f] is None for f in ("house", "apartment", "entrance", "floor", "city", "province")), (raw, r)
        assert r["confidence"] <= LOW_CONFIDENCE, (raw, r["confidence"])


@pytest.mark.parametrize("raw", ["", " ", "   ", "\t\n\r\n", "\u200b\u200c\ufeff"])
def test_blank_raw_gives_all_null_components_and_low_confidence(raw):
    r = parse_one(raw)
    assert not shape_violations(r)
    assert all(r[k] is None for k in COMPONENTS), r
    assert r["raw_address"] == raw
    assert r["confidence"] <= LOW_CONFIDENCE


# ----------------------------------------------------------------------------- C4/C5 echo & preservation
ECHO_RAWS = [
    "  ул. Мира 5  ", "ул. Мира 5\r\nкв 3", "ул.   Мира   5", "\tул. Мира 5\n", "ул.\u00a0Мира\u00a05", "\u200bул. Мира 5\u200b",
    "\ufeffул. Мира 5", "ул. Мира 5 \u0438\u0306", "ул. Мира 5 \u00e9 / e\u0301", "УЛ. МИРА 5", "ул. мира 5", "🏠 ул. Мира 5",
    "ул. Мира\x00 5", "Ақтөбе қаласы, Абай көшесі 5",
]


@pytest.mark.parametrize("raw", ECHO_RAWS, ids=[repr(r)[:28] for r in ECHO_RAWS])
def test_raw_address_is_echoed_code_point_identical(raw):
    out = parse_one(raw)["raw_address"]
    assert out == raw and len(out) == len(raw) and type(out) is str, f"{out!r} != {raw!r}"


def test_nfc_nfd_are_not_normalised_in_echo():
    import unicodedata
    nfc = unicodedata.normalize("NFC", "ул. Мира 5 й")
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd
    assert parse_one(nfc)["raw_address"] == nfc
    assert parse_one(nfd)["raw_address"] == nfd


@pytest.mark.parametrize("rid", [0, 1, -7, 2**53 + 1, 10**20, "x", "007", " padded ", "Ақтөбе-1", "a" * 300])
def test_id_is_echoed_without_type_coercion(rid):
    from app.parser import parse_batch
    out = parse_batch([{"id": rid, "raw_address": "ул. Мира 5"}])[0]
    assert out["id"] == rid and type(out["id"]) is type(rid)


def test_extra_input_keys_are_ignored_and_not_serialised():
    from app.parser import parse_batch
    out = parse_batch([{"id": 1, "raw_address": "ул. Мира 5", "city": "Астана", "house": "999", "note": "x", "extra": [1]}])[0]
    assert list(out.keys()) == OUTPUT_KEYS
    assert out["city"] is None and out["house"] == "5"


# ----------------------------------------------------------------------------- id-invariance / independence
INVARIANCE_RAWS = corpus_acceptance() + corpus_holdout_raws()[:40]


def test_changing_id_never_changes_components_or_confidence():
    from app.parser import parse_batch
    a = parse_batch([{"id": f"id-A-{i}", "raw_address": r} for i, r in enumerate(INVARIANCE_RAWS)])
    b = parse_batch([{"id": 10**6 + i, "raw_address": r} for i, r in enumerate(INVARIANCE_RAWS)])
    for raw, x, y in zip(INVARIANCE_RAWS, a, b):
        assert components(x) == components(y), raw


def test_no_lookup_by_id_same_id_with_different_addresses():
    from app.parser import parse_batch
    out = parse_batch([{"id": "dup", "raw_address": "ул. Мира 5"}, {"id": "dup", "raw_address": "проспект Абая 10, кв 2"},
                       {"id": "dup", "raw_address": "ул. Мира 5"}])
    assert [o["id"] for o in out] == ["dup"] * 3
    assert out[0]["house"] == "5" and out[1]["house"] == "10" and out[1]["apartment"] == "2"
    assert components(out[0]) == components(out[2]) != components(out[1])


def test_batch_context_does_not_influence_a_record():
    """A record parsed alone, in a batch, and in the reversed batch gives the same components (no cross-record state)."""
    from app.parser import parse_batch
    raws = INVARIANCE_RAWS
    solo = [parse_batch([{"id": "s", "raw_address": r}])[0] for r in raws]
    batch = parse_batch([{"id": f"b{i}", "raw_address": r} for i, r in enumerate(raws)])
    rev = parse_batch([{"id": f"b{i}", "raw_address": r} for i, r in reversed(list(enumerate(raws)))])[::-1]
    for r, s_, b_, v_ in zip(raws, solo, batch, rev):
        assert components(s_) == components(b_) == components(v_), r


def test_output_order_follows_input_order_after_permutation():
    import random
    from app.parser import parse_batch
    recs = [{"id": f"p{i}", "raw_address": r} for i, r in enumerate(INVARIANCE_RAWS)]
    perm = list(range(len(recs)))
    random.Random(20261017).shuffle(perm)
    out = parse_batch([recs[i] for i in perm])
    assert [o["id"] for o in out] == [recs[i]["id"] for i in perm]
    assert [o["raw_address"] for o in out] == [recs[i]["raw_address"] for i in perm]


def test_cardinality_with_duplicates_and_empty_batch():
    from app.parser import parse_batch
    assert parse_batch([]) == []
    recs = [{"id": "d", "raw_address": "ул. Мира 5"}] * 7
    out = parse_batch(recs)
    assert len(out) == 7 and len({str(sorted(o.items())) for o in out}) == 1, "duplicates are not deduplicated"


def test_large_batch_cardinality_and_order():
    from app.parser import parse_batch
    raws = (corpus_acceptance() + corpus_holdout_raws()) * 12
    recs = [{"id": i, "raw_address": r} for i, r in enumerate(raws)]
    with time_limit(60):
        out = parse_batch(recs)
    assert [o["id"] for o in out] == list(range(len(recs)))


# ----------------------------------------------------------------------------- no-fabrication provenance (C5)
@pytest.mark.parametrize("name,loader", [("acceptance", corpus_acceptance), ("holdout", corpus_holdout_raws),
                                          ("repo_data", corpus_repo_data), ("adversarial", lambda: [r for _, r in ADVERSARIAL_SAFE])])
def test_street_landmark_and_numbers_are_fragments_of_the_input(name, loader):
    from app.parser import parse_batch
    raws = loader()
    out = parse_batch([{"id": i, "raw_address": r} for i, r in enumerate(raws)])
    bad = [(raws[i], v) for i, o in enumerate(out) if (v := fragment_violations(raws[i], o))]
    assert not bad, f"{len(bad)} fabricated values, first: {bad[:3]}"


def test_city_and_province_come_from_the_gazetteer_only():
    import app.geo as geo
    data = geo.get_data()
    cities = {c["name"] for c in data["cities"]}
    provinces = set(data["provinces"])
    from app.parser import parse_batch
    raws = corpus_all_safe()
    for raw, o in zip(raws, parse_batch([{"id": i, "raw_address": r} for i, r in enumerate(raws)])):
        assert o["city"] is None or o["city"] in cities, (raw, o["city"])
        assert o["province"] is None or o["province"] in provinces | cities, (raw, o["province"])


# ----------------------------------------------------------------------------- C7 totality & the last barrier
def test_parse_address_is_total_on_valid_records():
    """parse_address must not raise on any VALID record (string raw_address)."""
    from app.parser import parse_address
    for name, raw in ADVERSARIAL_SAFE:
        with time_limit(5):
            r = parse_address({"id": name, "raw_address": raw})
        assert not shape_violations(r), name


@pytest.mark.defect("C7-LOG")
def test_internal_failure_is_logged_without_payload_and_returns_500(monkeypatch, caplog):
    """Owner C7 release delta: HTTP boundary logs once; no successful partial batch."""
    import app.parser as parser
    secret = "СЕКРЕТНЫЙ-МАРКЕР-9137"
    real = parser.parse_address

    def flaky(record):
        if secret in record["raw_address"]:
            raise RuntimeError("synthetic failure for QA")
        return real(record)

    monkeypatch.setattr(parser, "parse_address", flaky)
    recs = [{"id": "a", "raw_address": "ул. Мира 5"}, {"id": "b", "raw_address": f"{secret} ул. Мира 5"},
            {"id": "c", "raw_address": "ул. Абая 7"}]
    from fastapi.testclient import TestClient
    from app.main import app
    with caplog.at_level(logging.ERROR):
        response = TestClient(app).post('/api/v1/addresses/parse-batch', json=recs)
    assert response.status_code == 500
    assert response.json() == {'detail': {'code': 'internal_error'}}
    assert 'X-Jana-Invalid-Items' not in response.headers
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and errors[0].message == 'parse_system_failure'
    assert errors[0].error_type == 'RuntimeError'
    rendered = "\n".join(r.getMessage() + " " + str(r.args) + " " + (r.exc_text or "") for r in caplog.records)
    assert secret not in rendered, "log must carry metadata only, not the address payload"
