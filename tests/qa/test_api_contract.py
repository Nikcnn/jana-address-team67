"""REST contract C1/C2/C3-v1.1/C7 through FastAPI TestClient (in-process, no sockets).

Normative text: workspace/contracts/JANA_ADDRESS_CONTRACT_v1.1.md (C3-v1.1 replaces the v1.0 «atomic reject»).
Expectations come from the contract, NOT from the implementation.

C3-v1.1 in one paragraph: envelope-level failures (400 invalid_json, 422 invalid_envelope, 413 limits,
400 invalid_jsonl+line) still reject the whole request. Every ELEMENT of the records array yields exactly one
14-field object at the same index. Items that cannot be parsed become «null-records»
(п.2: non-object / raw_address missing, not a string or containing a lone surrogate -> raw_address null;
п.3: raw_address longer than 3000 chars -> raw_address echoed verbatim), all 11 components null,
confidence 0.0. An invalid `id` alone (п.1) is `id: null` and the record is parsed normally.
Header `X-Jana-Invalid-Items: <N>` is present only when N >= 1 null-records were produced (п.5).

Open points deliberately NOT asserted (contract is silent): the exact 413 `code` of too_many_items/upload,
the shape of a single-record envelope without a `raw_address` key (`{"id": 1}`), the HTTP behaviour for NaN/Infinity
literals and for integers beyond Python's int-from-str digit limit.
"""
from __future__ import annotations

import json
import logging
import random
import re
from typing import Any

import pytest

from .helpers import (COMPONENTS, LOW_CONFIDENCE, OUTPUT_KEYS, components, corpus_acceptance, corpus_holdout_raws,
                      shape_violations)

URL = "/api/v1/addresses/parse-batch"
ALIASES = ["/api/v1/addresses/parse-batch", "/api/parse-batch", "/parse"]
UPLOAD_URL = "/api/v1/addresses/upload"
HEADER = "X-Jana-Invalid-Items"
SENTINEL = "ЭХО-МАРКЕР-4417"          # must never come back for an invalid item / inside an error response
GOOD = {"id": "g1", "raw_address": "ул. Мира 5, кв 3"}
OK1 = {"id": "ok1", "raw_address": "ул. Мира 5"}
OK2 = {"id": "ok2", "raw_address": "ул. Абая 7"}
MISSING = object()                    # marker: the key must be omitted from the generated item

# C7: item-level error codes of the v1.0 atomic reject must no longer reach the client.
ITEM_ERROR_TOKENS = ("invalid_items", "item_not_object", "raw_address_missing", "raw_address_not_string",
                     "raw_address_too_long", "raw_address_invalid_unicode", "id_missing", "id_invalid_type",
                     "id_invalid_unicode", '"errors"', '"detail"')


def post(client, body: Any, url: str = URL):
    return client.post(url, content=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                       headers={"content-type": "application/json"})


def post_text(client, text: str, url: str = URL):
    """POST a hand-written JSON text (needed for `\\ud800` escapes that json.dumps(ensure_ascii=False) cannot encode)."""
    return client.post(url, content=text.encode("utf-8"), headers={"content-type": "application/json"})


def upload(client, data: bytes, name: str = "t.json"):
    return client.post(UPLOAD_URL, files={"file": (name, data, "application/json")})


def assert_200(r) -> None:
    """C3-v1.1: a defective item must never turn into an HTTP error (v1.0 answered 422 invalid_items)."""
    assert r.status_code == 200, f"item-level handling expected HTTP 200, got {r.status_code}: {r.text[:200]}"


def post_ok(client, body: Any, url: str = URL):
    r = post(client, body, url)
    assert_200(r)
    return r


def solo(client, item: Any) -> dict:
    return post_ok(client, [item]).json()[0]


def err_code(r) -> Any:
    detail = r.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else detail


def ints_in(obj: Any) -> list[int]:
    if isinstance(obj, bool):
        return []
    if isinstance(obj, int):
        return [obj]
    if isinstance(obj, dict):
        return [i for v in obj.values() for i in ints_in(v)]
    if isinstance(obj, list):
        return [i for v in obj for i in ints_in(v)]
    return []


def rec_item(rid: Any, raw: Any) -> dict:
    item: dict[str, Any] = {}
    if rid is not MISSING:
        item["id"] = rid
    if raw is not MISSING:
        item["raw_address"] = raw
    return item


def null_record(rid: Any = None, raw: Any = None) -> dict:
    """Expected null-record (C3-v1.1 п.2/п.3): C4 key order, 11 null components, confidence 0.0."""
    return {"id": rid, "raw_address": raw, **{k: None for k in COMPONENTS}, "confidence": 0.0}


def assert_null_record(rec: dict, rid: Any = None, raw: Any = None) -> None:
    assert list(rec.keys()) == OUTPUT_KEYS, f"keys/order {list(rec.keys())}"
    assert rec == null_record(rid, raw), rec
    assert type(rec["confidence"]) is float, f"confidence must be float 0.0, got {rec['confidence']!r}"
    assert type(rec["id"]) is type(rid) and type(rec["raw_address"]) is type(raw), (rec["id"], rec["raw_address"])
    assert not shape_violations(rec)


def no_item_error_leak(text: str) -> None:
    for token in ITEM_ERROR_TOKENS:
        assert token not in text, f"v1.0 item-error token {token!r} leaked to the client: {text[:200]}"


# ----------------------------------------------------------------------------- C1 routes / envelopes
@pytest.mark.parametrize("url", ALIASES)
def test_all_parse_batch_aliases_exist(client, url):
    r = post(client, [GOOD], url)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list) and len(r.json()) == 1


@pytest.mark.parametrize("key", ["items", "records", "addresses"])
def test_wrapper_envelope_is_mirrored(client, key):
    items = [GOOD, {"id": 2, "raw_address": "проспект Абая 10"}]
    r = post(client, {key: items})
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, dict) and list(body) == [key] and len(body[key]) == 2


def test_array_in_array_out(client):
    r = post(client, [GOOD])
    assert r.status_code == 200 and isinstance(r.json(), list)


def test_single_object_in_single_object_out(client):
    r = post(client, GOOD)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, dict) and list(body.keys()) == OUTPUT_KEYS and body["id"] == "g1"


def test_empty_batch_is_200_empty_array(client):
    r = post(client, [])
    assert r.status_code == 200 and r.json() == []


@pytest.mark.parametrize("key", ["items", "records", "addresses"])
def test_empty_wrapper_batch_is_200_empty_list(client, key):
    r = post(client, {key: []})
    assert r.status_code == 200 and r.json() == {key: []}


def test_every_response_record_has_exactly_14_ordered_fields(client):
    items = [{"id": i, "raw_address": raw} for i, raw in enumerate(corpus_acceptance())]
    r = post(client, items)
    assert r.status_code == 200
    bad = [(i, v) for i, rec in enumerate(r.json()) if (v := shape_violations(rec))]
    assert not bad, bad[:3]


def test_response_is_strict_json_with_no_nan_or_infinity(client):
    r = post(client, [GOOD])
    json.loads(r.content, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    assert r.headers["content-type"].startswith("application/json")


def test_health_is_ok_and_json(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json().get("status") == "ok"


def test_demo_endpoint_returns_items(client):
    r = client.get("/api/v1/addresses/demo")
    assert r.status_code == 200 and isinstance(r.json().get("items"), list) and r.json()["items"]


def test_openapi_json_is_served(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200 and "paths" in r.json()


# ----------------------------------------------------------------------------- C2/C4 echo & ordering
def test_extra_keys_are_ignored_and_not_serialised(client):
    r = post(client, [{"id": 1, "raw_address": "ул. Мира 5", "city": "Астана", "house": "999", "x": {"y": 1}}])
    rec = r.json()[0]
    assert list(rec.keys()) == OUTPUT_KEYS and rec["city"] is None and rec["house"] == "5"


@pytest.mark.parametrize("rid", [0, 7, -3, 2**53 + 1, 10**20, "007", "abc", " pad ", "", "🏠", "Ақтөбе-1"])
def test_id_type_is_preserved_int_stays_int_str_stays_str(client, rid):
    rec = post(client, [{"id": rid, "raw_address": "ул. Мира 5"}]).json()[0]
    assert rec["id"] == rid and type(rec["id"]) is type(rid)


def test_raw_address_echo_is_codepoint_identical_over_http(client):
    raws = ["  ул. Мира 5  ", "ул. Мира 5\r\nкв 3", "ул.\u00a0Мира\u00a05", "\u200bул. Мира 5", "Ақтөбе қаласы, Абай көшесі 5 🏠", "ул. Мира 5 \u0438\u0306"]
    out = post(client, [{"id": i, "raw_address": r} for i, r in enumerate(raws)]).json()
    assert [o["raw_address"] for o in out] == raws


def test_duplicate_ids_and_rows_are_not_deduplicated_and_order_is_kept(client):
    body = [{"id": "d", "raw_address": "ул. Мира 5"}, {"id": "d", "raw_address": "проспект Абая 10, кв 2"},
            {"id": "d", "raw_address": "ул. Мира 5"}, {"id": "e", "raw_address": "ул. Мира 5"}]
    out = post(client, body).json()
    assert [o["id"] for o in out] == ["d", "d", "d", "e"]
    assert out[0]["house"] == "5" and out[1]["house"] == "10"
    assert components(out[0]) == components(out[2]) == components(out[3])


def test_permuted_input_gives_permuted_output(client):
    items = [{"id": f"i{i}", "raw_address": raw} for i, raw in enumerate(corpus_acceptance())]
    base = {o["id"]: components(o) for o in post(client, items).json()}
    shuffled = items[:]
    random.Random(7).shuffle(shuffled)
    out = post(client, shuffled).json()
    assert [o["id"] for o in out] == [i["id"] for i in shuffled]
    assert {o["id"]: components(o) for o in out} == base


def test_id_mutation_does_not_change_components(client):
    raws = corpus_acceptance()
    a = post(client, [{"id": f"A{i}", "raw_address": r} for i, r in enumerate(raws)]).json()
    b = post(client, [{"id": 900000 + i, "raw_address": r} for i, r in enumerate(raws)]).json()
    assert [components(x) for x in a] == [components(y) for y in b]


def test_repeated_requests_are_byte_identical(client):
    items = [{"id": i, "raw_address": r} for i, r in enumerate(corpus_acceptance())]
    first = post(client, items).content
    assert all(post(client, items).content == first for _ in range(3))


# ============================================================================= C3-v1.1 item-level handling
# (id of the expected null-record is given by the п.1 rule: echo a str/int id of an OBJECT, otherwise null)
NULL_ITEMS = [
    # п.2: the element is not a JSON object -> id null
    pytest.param(5, None, id="item-int"),
    pytest.param(1.5, None, id="item-float"),
    pytest.param(True, None, id="item-bool"),
    pytest.param("строка", None, id="item-string"),
    pytest.param("", None, id="item-empty-string"),
    pytest.param(None, None, id="item-null"),
    pytest.param([1, 2], None, id="item-list"),
    pytest.param([], None, id="item-empty-list"),
    pytest.param([{"id": 1, "raw_address": "ул. Мира 5"}], None, id="item-list-wrapping-a-valid-record"),
    # п.2: an object whose raw_address is missing / not a string -> id by the п.1 rule
    pytest.param({}, None, id="empty-object"),
    pytest.param({"id": 1}, 1, id="raw-missing"),
    pytest.param({"id": "abc"}, "abc", id="raw-missing-str-id"),
    pytest.param({"id": 1, "raw_address": 34}, 1, id="raw-int"),
    pytest.param({"id": 1, "raw_address": 1.5}, 1, id="raw-float"),
    pytest.param({"id": 1, "raw_address": True}, 1, id="raw-bool"),
    pytest.param({"id": 1, "raw_address": None}, 1, id="raw-null"),
    pytest.param({"id": 1, "raw_address": ["ул. Мира 5"]}, 1, id="raw-list"),
    pytest.param({"id": 1, "raw_address": {"a": 1}}, 1, id="raw-object"),
    pytest.param({"id": 1, "address": "ул. Мира 5"}, 1, id="raw-under-another-key"),
    pytest.param({"id": 1, "Raw_Address": "ул. Мира 5"}, 1, id="raw-key-wrong-case"),
    # п.2 + п.1: both raw_address and id are bad -> id null as well
    pytest.param({"raw_address": 34}, None, id="raw-int-id-missing"),
    pytest.param({"id": 1.5, "raw_address": 34}, None, id="raw-int-id-float"),
    pytest.param({"id": True, "raw_address": None}, None, id="raw-null-id-bool"),
    pytest.param({"id": None, "raw_address": [1]}, None, id="raw-list-id-null"),
    pytest.param({"id": {"a": 1}, "raw_address": {"b": 2}}, None, id="raw-object-id-object"),
]

INVALID_IDS = [
    pytest.param(MISSING, id="missing"), pytest.param(None, id="null"), pytest.param(1.5, id="float"),
    pytest.param(1.0, id="float-integral"), pytest.param(True, id="bool-true"), pytest.param(False, id="bool-false"),
    pytest.param([1], id="list"), pytest.param([], id="empty-list"), pytest.param({"a": 1}, id="object"),
    pytest.param({}, id="empty-object"),
]


@pytest.mark.defect("C3-v1.1")
@pytest.mark.parametrize("bad,rid", NULL_ITEMS)
def test_invalid_item_becomes_null_record_and_batch_is_200(client, bad, rid):
    r = post(client, [OK1, bad, OK2])
    assert_200(r)
    assert r.headers["content-type"].startswith("application/json")
    out = r.json()
    assert isinstance(out, list) and len(out) == 3, "exactly one output per input element"
    assert out[0] == solo(client, OK1) and out[2] == solo(client, OK2), "neighbours must be unaffected"
    assert out[0]["house"] == "5" and out[2]["house"] == "7"
    assert_null_record(out[1], rid)
    assert r.headers.get(HEADER) == "1"
    no_item_error_leak(r.text)


@pytest.mark.defect("C3-v1.1")
@pytest.mark.parametrize("bad,rid", NULL_ITEMS)
def test_null_record_has_exactly_14_ordered_keys_all_components_null_and_confidence_0_0(client, bad, rid):
    """A null-record is never shorter/longer than 14 fields and never leaks a component value."""
    r = post(client, [bad])
    assert_200(r)
    rec = r.json()[0]
    assert len(rec) == 14 and list(rec.keys()) == OUTPUT_KEYS
    assert [rec[k] for k in COMPONENTS] == [None] * 11
    assert rec["confidence"] == 0.0 and type(rec["confidence"]) is float
    assert re.search(r'"confidence"\s*:\s*0\.0(?![\d])', r.text), f"confidence must be serialised as 0.0: {r.text[:200]}"
    assert rec["raw_address"] is None and rec["id"] == rid and type(rec["id"]) is type(rid)


@pytest.mark.defect("C3-v1.1")
@pytest.mark.parametrize("pos", range(5))
def test_bad_item_keeps_its_own_index_at_every_position(client, pos):
    items = [{"id": f"v{i}", "raw_address": f"ул. Мира {i + 1}"} for i in range(5)]
    items[pos] = {"id": f"v{pos}", "raw_address": 34}
    r = post(client, items)
    assert_200(r)
    out = r.json()
    assert [o["id"] for o in out] == [f"v{i}" for i in range(5)]
    for i, rec in enumerate(out):
        if i == pos:
            assert_null_record(rec, f"v{pos}")
        else:
            assert rec["house"] == str(i + 1) and rec["raw_address"] == f"ул. Мира {i + 1}"
    assert r.headers.get(HEADER) == "1"


@pytest.mark.defect("C3-v1.1")
def test_mixed_batch_cardinality_order_ids_and_header_count(client):
    """Index 1 and 4 are null-records; index 3 has a valid raw_address but no id -> normal parse with id null."""
    r = post(client, [GOOD, {"id": 1}, GOOD, {"raw_address": "x"}, 5])
    assert_200(r)
    out = r.json()
    assert len(out) == 5
    assert out[0] == out[2] and out[0]["id"] == "g1"
    assert_null_record(out[1], 1)
    assert out[3]["id"] is None and out[3]["raw_address"] == "x" and list(out[3]) == OUTPUT_KEYS
    assert_null_record(out[4], None)
    assert r.headers.get(HEADER) == "2", "only п.2/п.3 null-records are counted; an id-only defect is not"


@pytest.mark.defect("C3-v1.1")
def test_duplicates_of_bad_and_good_items_are_not_deduplicated(client):
    bad = {"id": "d", "raw_address": 34}
    good = {"id": "d", "raw_address": "ул. Мира 5"}
    r = post(client, [bad, good, bad, good, bad])
    out = r.json()
    assert_200(r)
    assert len(out) == 5 and [o["id"] for o in out] == ["d"] * 5
    assert [o["raw_address"] for o in out] == [None, "ул. Мира 5", None, "ул. Мира 5", None]
    assert_null_record(out[0], "d")
    assert_null_record(out[2], "d")
    assert_null_record(out[4], "d")
    assert out[1] == out[3] and out[1]["house"] == "5"
    assert r.headers.get(HEADER) == "3"


@pytest.mark.defect("C3-v1.1")
def test_null_record_does_not_depend_on_its_neighbours(client):
    bad = {"id": 9, "raw_address": None}
    alone = solo(client, bad)
    inside = post_ok(client, [OK1, bad, OK2, bad]).json()
    assert alone == inside[1] == inside[3] == null_record(9)


@pytest.mark.defect("C3-v1.1")
def test_changing_the_id_of_a_null_record_changes_only_the_id(client):
    a = solo(client, {"id": "A", "raw_address": 34})
    b = solo(client, {"id": 123, "raw_address": 34})
    assert {k: v for k, v in a.items() if k != "id"} == {k: v for k, v in b.items() if k != "id"}
    assert a["id"] == "A" and b["id"] == 123 and type(b["id"]) is int


# --- п.1: an invalid id alone does NOT make a null-record --------------------------------------------------------
@pytest.mark.defect("C3-v1.1")
@pytest.mark.parametrize("rid", INVALID_IDS)
def test_invalid_id_gives_null_id_but_record_is_parsed_normally(client, rid):
    raws = corpus_acceptance()
    base = post(client, [{"id": i, "raw_address": r} for i, r in enumerate(raws)]).json()
    r = post(client, [rec_item(rid, raw) for raw in raws])
    assert_200(r)
    out = r.json()
    assert len(out) == len(raws)
    for rec, ref, raw in zip(out, base, raws):
        assert list(rec) == OUTPUT_KEYS and rec["id"] is None and rec["raw_address"] == raw
        assert components(rec) == components(ref), "an invalid id must not change components/confidence"
    assert r.headers.get(HEADER) is None, "an id-only defect is not a null-record (п.5 counts п.2–3 only)"


@pytest.mark.defect("C3-v1.1")
def test_valid_str_and_int_ids_are_still_echoed_next_to_invalid_ones(client):
    out = post_ok(client, [{"id": 5, "raw_address": "ул. Мира 5"}, {"id": 1.5, "raw_address": "ул. Мира 5"},
                           {"id": "5", "raw_address": "ул. Мира 5"}, {"id": True, "raw_address": "ул. Мира 5"}]).json()
    assert [o["id"] for o in out] == [5, None, "5", None] and type(out[0]["id"]) is int


# --- п.2/п.3 with lone surrogates (JSON text, because json.dumps cannot emit them with ensure_ascii=False) --------
def _surrogate_batch(raw_json: str, rid_json: str = "9") -> str:
    return ('[{"id":"ok1","raw_address":"ул. Мира 5"},{"id":' + rid_json + ',"raw_address":"' + raw_json
            + '"},{"id":"ok2","raw_address":"ул. Абая 7"}]')


@pytest.mark.defect("C3-v1.1")
@pytest.mark.parametrize("esc", ["\\ud800", "\\udbff", "\\udc00", "\\udfff", "\\udfe0\\ud83c", "\\ud83c"],
                         ids=["high", "high-max", "low", "low-max", "reversed-pair", "high-before-text"])
@pytest.mark.parametrize("where", ["start", "middle", "end"])
def test_lone_surrogate_in_raw_address_is_a_null_record(client, esc, where):
    raw = {"start": esc + " ул. Мира 5", "middle": "ул. " + esc + " Мира 5", "end": "ул. Мира 5 " + esc}[where]
    r = post_text(client, _surrogate_batch(raw))
    assert_200(r)
    out = r.json()          # must be valid JSON/UTF-8: a lone surrogate must not reach the serializer
    assert len(out) == 3 and out[0]["house"] == "5" and out[2]["house"] == "7"
    assert_null_record(out[1], 9)
    assert r.headers.get(HEADER) == "1"


@pytest.mark.defect("C3-v1.1")
def test_valid_surrogate_pair_is_a_normal_character_not_a_lone_surrogate(client):
    r = post_text(client, _surrogate_batch("ул. Мира 5 \\ud83c\\udfe0"))
    out = r.json()
    assert_200(r)
    assert out[1]["raw_address"] == "ул. Мира 5 🏠" and out[1]["house"] == "5"
    assert r.headers.get(HEADER) is None


@pytest.mark.defect("C3-v1.1")
def test_lone_surrogate_in_id_gives_null_id_and_normal_parse(client):
    r = post_text(client, '[{"id":"a\\ud800b","raw_address":"ул. Мира 5"},{"id":"\\ud83c\\udfe0","raw_address":"ул. Мира 5"}]')
    out = r.json()
    assert_200(r)
    assert len(out) == 2
    assert out[0]["id"] is None and out[0]["house"] == "5" and out[0]["raw_address"] == "ул. Мира 5"
    assert out[1]["id"] == "🏠" and out[1]["house"] == "5"
    assert r.headers.get(HEADER) is None


@pytest.mark.defect("C3-v1.1")
def test_lone_surrogate_in_both_id_and_raw_address_gives_null_id_and_null_raw(client):
    r = post_text(client, '[{"id":"\\ud800","raw_address":"\\udc00"}]')
    assert_200(r)
    assert_null_record(r.json()[0], None)
    assert r.headers.get(HEADER) == "1"


@pytest.mark.defect("C3-v1.1")
def test_lone_surrogate_as_a_bare_array_element_is_a_null_record(client):
    r = post_text(client, '["\\ud800", {"id":1,"raw_address":"ул. Мира 5"}]')
    out = r.json()
    assert_200(r)
    assert len(out) == 2
    assert_null_record(out[0], None)
    assert out[1]["id"] == 1 and out[1]["house"] == "5"


@pytest.mark.defect("C3-v1.1")
def test_lone_surrogate_in_ignored_places_does_not_invalidate_the_record(client):
    """Extra keys / key names are ignored (C2); only id and raw_address are inspected."""
    r = post_text(client, '[{"id":1,"raw_address":"ул. Мира 5","note":"\\ud800","\\udc00":2}]')
    out = r.json()
    assert_200(r)
    assert out[0]["id"] == 1 and out[0]["house"] == "5"
    assert r.headers.get(HEADER) is None


# --- п.3: raw_address longer than 3000 chars ---------------------------------------------------------------------
LONG_RAWS = [
    pytest.param("ы" * 3001, id="cyr-3001"),
    pytest.param("🏠" * 3001, id="astral-3001-codepoints"),
    pytest.param(" " * 3001, id="spaces-3001"),
    pytest.param("ул.\u00a0Мира\u200b 5\r\n" * 300, id="specials-3900"),
    pytest.param("ул. Мира 5, " * 1000, id="realistic-12000"),
    pytest.param("а" * 100_000, id="100k"),
]


@pytest.mark.defect("C3-v1.1")
@pytest.mark.parametrize("raw", LONG_RAWS)
def test_overlong_raw_address_is_a_null_record_with_verbatim_echo(client, raw):
    assert len(raw) > 3000
    r = post(client, [OK1, {"id": 7, "raw_address": raw, "city": "Астана"}, OK2])
    assert_200(r)
    out = r.json()
    assert len(out) == 3 and out[0]["house"] == "5" and out[2]["house"] == "7"
    assert_null_record(out[1], 7, raw)
    assert out[1]["raw_address"] == raw and len(out[1]["raw_address"]) == len(raw)
    assert r.headers.get(HEADER) == "1"
    no_item_error_leak(r.text)


@pytest.mark.defect("C3-v1.1")
def test_overlong_raw_address_with_invalid_id_has_null_id_and_echoed_raw(client):
    raw = "ы" * 3001
    rec = solo(client, {"id": 1.5, "raw_address": raw})
    assert_null_record(rec, None, raw)


@pytest.mark.defect("C3-v1.1")
@pytest.mark.parametrize("raw", ["ы" * 3000, "🏠" * 3000, " " * 3000], ids=["cyr-3000", "astral-3000", "spaces-3000"])
def test_exactly_3000_chars_is_still_the_normal_path(client, raw):
    """The limit is on code points, applied to the raw string (no trim): 3000 is valid, 3001 is not."""
    r = post(client, [{"id": 1, "raw_address": raw}])
    assert_200(r)
    assert r.json()[0]["raw_address"] == raw and not shape_violations(r.json()[0])
    assert r.headers.get(HEADER) is None, "3000 chars must not be counted as a null-record"


# --- п.6: empty string is a valid string ---------------------------------------------------------------------------
@pytest.mark.defect("C3-v1.1")
@pytest.mark.parametrize("raw", ["", " ", "\t\n"], ids=["empty", "space", "tab-newline"])
def test_empty_or_blank_raw_address_is_a_normal_record_not_a_null_record(client, raw):
    r = post(client, [{"id": 4, "raw_address": raw}])
    rec = r.json()[0]
    assert_200(r)
    assert rec["id"] == 4 and rec["raw_address"] == raw and not shape_violations(rec)
    assert all(rec[k] is None for k in COMPONENTS) and rec["confidence"] <= LOW_CONFIDENCE
    assert r.headers.get(HEADER) is None, "п.6: `\"\"` is valid, so N stays 0"


# --- п.5: header and log ----------------------------------------------------------------------------------------------
@pytest.mark.defect("C3-v1.1")
def test_header_counts_only_null_records_of_п2_and_п3(client):
    items = [5, None, {"id": 1}, {"id": 2, "raw_address": 34}, {"id": 3, "raw_address": "ы" * 3001},   # 5 null-records
             {"id": 1.5, "raw_address": "ул. Мира 5"}, {"id": 4, "raw_address": ""}, OK1]             # 3 normal ones
    r = post(client, items)
    assert_200(r)
    assert len(r.json()) == 8
    value = r.headers.get(HEADER)
    assert value == "5" and re.fullmatch(r"[1-9][0-9]*", value)


@pytest.mark.defect("C3-v1.1")
@pytest.mark.parametrize("body", [
    pytest.param([], id="empty-batch"), pytest.param([OK1, OK2], id="all-valid"),
    pytest.param([{"id": 1.5, "raw_address": "ул. Мира 5"}, {"raw_address": "ул. Мира 5"}], id="only-invalid-ids"),
    pytest.param([{"id": 1, "raw_address": ""}], id="only-empty-raw"),
    pytest.param(OK1, id="single-valid"), pytest.param({"items": [OK1]}, id="wrapper-valid"),
])
def test_no_header_when_no_null_record_was_produced(client, body):
    r = post(client, body)
    assert_200(r)
    assert HEADER not in r.headers and HEADER.lower() not in r.headers


@pytest.mark.defect("C3-v1.1")
@pytest.mark.parametrize("payload", [b"{", b"", b"\xff\xfe\x00", b'{"foo": []}', b"5"])
def test_no_header_on_envelope_level_errors(client, payload):
    r = client.post(URL, content=payload, headers={"content-type": "application/json"})
    assert r.status_code in (400, 422) and r.headers.get(HEADER) is None


STD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


def _render(rec: logging.LogRecord) -> str:
    """Everything a log line could carry: formatted message (+traceback) and structured `extra` fields."""
    extras = {k: v for k, v in vars(rec).items() if k not in STD_ATTRS}
    return logging.Formatter("%(message)s").format(rec) + " " + repr(extras)


def _degraded_logs(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno == logging.WARNING and "invalid_items_degraded" in _render(r)]


@pytest.mark.defect("C3-v1.1")
def test_degraded_log_has_only_count_and_at_most_20_indices_and_no_input_values(client, caplog):
    bad_idx = [211 + 7 * k for k in range(30)]               # 30 null-records at distinctive indices
    kinds = [SENTINEL, [SENTINEL], {"id": SENTINEL + "-id", "raw_address": None}, {"id": [SENTINEL], "raw_address": {"k": SENTINEL}},
             {"id": 3, "raw_address": SENTINEL + "ы" * 3001}, {"extra": SENTINEL}]
    items: list[Any] = [{"id": i, "raw_address": ""} for i in range(450)]
    for n, idx in enumerate(bad_idx):
        items[idx] = kinds[n % len(kinds)]
    with caplog.at_level(logging.DEBUG):
        r = post(client, items)
    assert_200(r)
    assert r.headers.get(HEADER) == "30"
    logs = _degraded_logs(caplog)
    assert logs, "WARNING `invalid_items_degraded` must be logged when N >= 1"
    text = "\n".join(_render(rec) for rec in logs)
    assert SENTINEL not in text and "ы" * 20 not in text, "log must not carry input values"
    numbers = {int(m) for m in re.findall(r"(?<![\d.])\d+(?![\d.])", text)}
    assert 30 in numbers, f"the number of null-records (30) must be logged: {text[:300]}"
    assert len(numbers & set(bad_idx)) <= 20, f"at most 20 indices may be logged: {sorted(numbers & set(bad_idx))}"


@pytest.mark.soft
def test_no_degraded_warning_when_nothing_was_degraded(client, caplog):
    """Contract only fixes the log for N >= 1; an N = 0 warning would be noise (QA inference, hence soft)."""
    with caplog.at_level(logging.DEBUG):
        post(client, [OK1, OK2, {"id": 1.5, "raw_address": "ул. Мира 5"}, {"id": 1, "raw_address": ""}])
    assert not _degraded_logs(caplog)


# --- no input echo (except verbatim raw of п.3 and ordinary records) ------------------------------------------------
@pytest.mark.defect("C3-v1.1")
def test_values_of_invalid_items_are_not_echoed_but_valid_records_and_overlong_raw_are_(client):
    valid_mark, long_mark = "ВАЛИД-МАРКЕР-1", "ДЛИННЫЙ-МАРКЕР-2"
    long_raw = long_mark + " " + "ы" * 3001
    body = [
        {"id": "ok1", "raw_address": f"{valid_mark} ул. Мира 5"},
        SENTINEL, [SENTINEL], {"k": SENTINEL},
        {"id": 1, "raw_address": {"k": SENTINEL}},
        {"id": 2, "raw_address": [SENTINEL]},
        {"id": {"k": SENTINEL}, "raw_address": 34},
        {"id": [SENTINEL], "raw_address": None, "extra": SENTINEL},
        {"id": 5.5, "raw_address": "ул. Абая 7", "extra": {"k": SENTINEL}, SENTINEL: SENTINEL},
        {"id": 8, "raw_address": long_raw, "extra": SENTINEL},
    ]
    r = post(client, body)
    assert_200(r)
    out = r.json()
    assert len(out) == len(body)
    assert SENTINEL not in r.text, "values of invalid items / ignored keys must not be echoed"
    assert not any(SENTINEL in str(v) for v in r.headers.values())
    assert valid_mark in out[0]["raw_address"] and out[9]["raw_address"] == long_raw
    assert out[8]["id"] is None and out[8]["house"] == "7" and out[8]["raw_address"] == "ул. Абая 7"


@pytest.mark.defect("C3-v1.1")
def test_values_of_surrogate_items_are_not_echoed(client):
    r = post_text(client, '[{"id":1,"raw_address":"\\ud800 ' + SENTINEL + '"},"\\ud800' + SENTINEL + '",{"id":"\\ud800' + SENTINEL
                  + '","raw_address":"ул. Мира 5"}]')
    out = r.json()
    assert_200(r)
    assert len(out) == 3
    assert SENTINEL not in r.text
    assert_null_record(out[0], 1)
    assert_null_record(out[1], None)
    assert out[2]["id"] is None and out[2]["house"] == "5"


# --- cardinality at scale ---------------------------------------------------------------------------------------------
def _thousand_valid() -> list[dict]:
    raws = corpus_acceptance() + corpus_holdout_raws()
    return [{"id": i, "raw_address": raws[i % len(raws)]} for i in range(1000)]


@pytest.fixture(scope="module")
def thousand_baseline(client):
    items = _thousand_valid()
    r = post(client, items)
    assert_200(r)
    assert len(r.json()) == 1000 and r.headers.get(HEADER) is None
    return items, r.json()


ONE_BAD_KINDS = [
    pytest.param(lambda i: None, lambda i: None, None, id="not-an-object"),
    pytest.param(lambda i: {"id": i, "raw_address": 34}, lambda i: i, None, id="raw-not-a-string"),
    pytest.param(lambda i: {"id": i, "raw_address": f"{i:04d} " + "ы" * 3001}, lambda i: i, "echo", id="raw-too-long"),
]


@pytest.mark.defect("C3-v1.1")
@pytest.mark.parametrize("idx", [0, 537, 999])
@pytest.mark.parametrize("make_bad,expected_id,raw_kind", ONE_BAD_KINDS)
def test_one_bad_item_among_1000_999_are_parsed_and_the_bad_one_is_at_its_own_index(
        client, thousand_baseline, idx, make_bad, expected_id, raw_kind):
    items, base = thousand_baseline
    mixed = list(items)
    mixed[idx] = make_bad(idx)
    r = post(client, mixed)
    assert_200(r)
    out = r.json()
    assert len(out) == 1000
    others = [i for i in range(1000) if i != idx]
    assert [out[i] for i in others] == [base[i] for i in others], "the 999 valid records must be parsed exactly as in a clean batch"
    assert_null_record(out[idx], expected_id(idx), mixed[idx]["raw_address"] if raw_kind == "echo" else None)
    assert r.headers.get(HEADER) == "1"


def _bad_item(i: int) -> tuple[Any, Any, Any]:
    """(input element, expected id, expected raw_address) for the i-th element of the «all bad» batch."""
    k = i % 10
    if k == 0:
        return i, None, None
    if k == 1:
        return None, None, None
    if k == 2:
        return [i], None, None
    if k == 3:
        return f"строка {i}", None, None
    if k == 4:
        return {"id": i}, i, None
    if k == 5:
        return {"id": f"s{i}", "raw_address": i}, f"s{i}", None
    if k == 6:
        return {"id": i, "raw_address": None}, i, None
    if k == 7:
        return {"raw_address": {"k": i}}, None, None
    if k == 8:
        return {"id": 1.5, "raw_address": [i]}, None, None
    raw = f"{i:04d} " + "ы" * 3001
    return {"id": i, "raw_address": raw}, i, raw


@pytest.mark.defect("C3-v1.1")
def test_all_1000_items_bad_gives_1000_null_records_and_200(client):
    built = [_bad_item(i) for i in range(1000)]
    r = post(client, [b[0] for b in built])
    assert_200(r)
    out = r.json()
    assert len(out) == 1000
    wrong = [i for i, (rec, (_, rid, raw)) in enumerate(zip(out, built))
             if list(rec) != OUTPUT_KEYS or rec != null_record(rid, raw) or type(rec["confidence"]) is not float
             or type(rec["id"]) is not type(rid)]
    assert not wrong, f"{len(wrong)} wrong records, first indices {wrong[:5]}: {out[wrong[0]] if wrong else ''}"
    assert r.headers.get(HEADER) == "1000"
    assert r.content == post(client, [b[0] for b in built]).content, "repeat must be byte-identical"


@pytest.mark.defect("C3-v1.1")
def test_10000_bad_items_are_accepted_and_10001_are_413(client):
    r = post(client, [None] * 10000)
    assert_200(r)
    assert len(r.json()) == 10000 and r.headers.get(HEADER) == "10000"
    assert all(rec == null_record() for rec in r.json())
    assert post(client, [None] * 10001).status_code == 413, "the 10000-item cap counts elements, valid or not"


@pytest.mark.defect("C3-v1.1")
def test_random_mix_of_valid_and_invalid_items_keeps_cardinality_order_and_kinds(client):
    rng = random.Random(20261018)
    raws = corpus_acceptance()
    base = {i: r for i, r in enumerate(post(client, [{"id": i, "raw_address": raw} for i, raw in enumerate(raws)]).json())}
    items: list[Any] = []
    plan: list[tuple[str, Any, Any, int | None]] = []        # (kind, expected id, expected raw, index into raws)
    bad_pool = [_bad_item(i) for i in range(10)]
    for n in range(300):
        if rng.random() < 0.4:
            item, rid, raw = bad_pool[rng.randrange(10)]
            items.append(item)
            plan.append(("null", rid, raw, None))
        else:
            j = rng.randrange(len(raws))
            items.append({"id": f"n{n}", "raw_address": raws[j]})
            plan.append(("ok", f"n{n}", raws[j], j))
    r = post(client, items)
    assert_200(r)
    assert len(r.json()) == 300
    nulls = 0
    for n, (rec, (kind, rid, raw, j)) in enumerate(zip(r.json(), plan)):
        if kind == "null":
            nulls += 1
            assert_null_record(rec, rid, raw)
        else:
            assert rec["id"] == rid and rec["raw_address"] == raw and components(rec) == components(base[j]), n
    assert r.headers.get(HEADER) == str(nulls)


# --- idempotence -------------------------------------------------------------------------------------------------------
def _mixed_body() -> list[Any]:
    return [OK1, 5, {"id": 2, "raw_address": 34}, {"id": 1.5, "raw_address": "ул. Мира 5"}, {"id": 3, "raw_address": "ы" * 3001},
            OK2, None, {"raw_address": ""}, [OK1], OK1]


@pytest.mark.defect("C3-v1.1")
def test_mixed_batch_repeat_is_byte_identical_including_the_header(client):
    first = post(client, _mixed_body())
    assert_200(first)
    assert first.headers.get(HEADER) == "5"
    for _ in range(3):
        again = post(client, _mixed_body())
        assert again.content == first.content and again.headers.get(HEADER) == first.headers.get(HEADER)


@pytest.mark.defect("C3-v1.1")
def test_repeating_a_batch_inside_itself_gives_the_same_per_item_results(client):
    """Doubling the batch must give the same answer for both halves (no state carried between occurrences)."""
    once = post_ok(client, _mixed_body()).json()
    twice = post_ok(client, _mixed_body() + _mixed_body())
    assert twice.json() == once + once and twice.headers.get(HEADER) == "10"


@pytest.mark.defect("C3-v1.1")
@pytest.mark.parametrize("route", ALIASES)
def test_item_level_handling_applies_to_every_alias(client, route):
    ref = post(client, _mixed_body(), URL)
    r = post(client, _mixed_body(), route)
    assert_200(r)
    assert r.content == ref.content and r.headers.get(HEADER) == "5"
    assert len(r.json()) == 10


@pytest.mark.defect("C3-v1.1")
@pytest.mark.parametrize("route", ALIASES)
def test_wrong_item_does_not_reject_the_batch_on_any_alias(client, route):
    r = post(client, [GOOD, {"id": 1, "raw_address": 34}], route)
    assert_200(r)
    assert len(r.json()) == 2
    assert_null_record(r.json()[1], 1)


# --- envelopes with invalid items --------------------------------------------------------------------------------------
@pytest.mark.defect("C3-v1.1")
@pytest.mark.parametrize("key", ["items", "records", "addresses"])
def test_wrapper_with_invalid_items_is_mirrored_with_one_object_per_element(client, key):
    r = post(client, {key: [OK1, 5, {"id": 3}, None, OK2]})
    assert_200(r)
    body = r.json()
    assert list(body) == [key] and len(body[key]) == 5
    out = body[key]
    assert out[0]["house"] == "5" and out[4]["house"] == "7"
    assert_null_record(out[1], None)
    assert_null_record(out[2], 3)
    assert_null_record(out[3], None)
    assert r.headers.get(HEADER) == "3"


@pytest.mark.defect("C3-v1.1")
@pytest.mark.parametrize("single,rid,raw,is_null", [
    pytest.param({"id": 5, "raw_address": 34}, 5, None, True, id="raw-int"),
    pytest.param({"id": "s", "raw_address": None}, "s", None, True, id="raw-null"),
    pytest.param({"id": "s", "raw_address": ["ул. Мира 5"]}, "s", None, True, id="raw-list"),
    pytest.param({"raw_address": None}, None, None, True, id="raw-null-id-missing"),
    pytest.param({"id": 1.5, "raw_address": {"a": 1}}, None, None, True, id="raw-object-id-float"),
    pytest.param({"id": 1, "raw_address": "ы" * 3001}, 1, "ы" * 3001, True, id="raw-too-long"),
    pytest.param({"raw_address": "ул. Мира 5"}, None, "ул. Мира 5", False, id="id-missing-raw-ok"),
    pytest.param({"id": [1], "raw_address": "ул. Мира 5"}, None, "ул. Мира 5", False, id="id-list-raw-ok"),
])
def test_single_record_with_a_defect_is_one_object_in_one_object_out(client, single, rid, raw, is_null):
    r = post(client, single)
    assert_200(r)
    rec = r.json()
    assert isinstance(rec, dict) and list(rec.keys()) == OUTPUT_KEYS
    if is_null:
        assert_null_record(rec, rid, raw)
        assert r.headers.get(HEADER) == "1"
    else:
        assert rec["id"] is None and rec["raw_address"] == raw and rec["house"] == "5"
        assert r.headers.get(HEADER) is None


@pytest.mark.defect("C3-v1.1")
@pytest.mark.parametrize("route", ALIASES)
def test_single_invalid_record_on_every_alias(client, route):
    r = post(client, {"id": 5, "raw_address": 34}, route)
    assert_200(r)
    assert r.json() == null_record(5) and r.headers.get(HEADER) == "1"


@pytest.mark.defect("C3-v1.1")
def test_extra_keys_never_rescue_nor_fail_a_record(client):
    out = post_ok(client, [{"id": 1, "raw_address": "ул. Мира 5", "deep": [[[[[[[[[[1]]]]]]]]]], "o": {"id": None, "raw_address": 5}},
                           {"id": 2, "raw_address": 34, "city": "Астана", "house": "99"}]).json()
    assert out[0]["id"] == 1 and out[0]["house"] == "5" and list(out[0]) == OUTPUT_KEYS
    assert_null_record(out[1], 2)


@pytest.mark.defect("C3-v1.1")
def test_item_level_error_codes_are_no_longer_returned_to_the_client(client):
    """C7-v1.1: item error codes (`raw_address_not_string`, `id_missing`, ...) are not returned to the client any more."""
    r = post(client, [5, {"id": 1}, {"id": 1, "raw_address": 34}, {"raw_address": "x"}, {"id": 1.5, "raw_address": "x"}])
    assert r.status_code == 200
    no_item_error_leak(r.text)
    assert not any(t in " ".join(r.headers.values()) for t in ("invalid_items", "raw_address_"))


# ----------------------------------------------------------------------------- envelope-level errors (UNCHANGED)
@pytest.mark.parametrize("payload", [b"{", b'{"items": [', b"", b"\xff\xfe\x00", b"not json", b"[1,2,"])
def test_malformed_json_is_400_without_echo(client, payload):
    r = client.post(URL, content=payload + SENTINEL.encode(), headers={"content-type": "application/json"})
    assert r.status_code == 400, f"{r.status_code}: {r.text[:200]}"
    assert err_code(r) == "invalid_json"
    assert SENTINEL not in r.text


@pytest.mark.parametrize("depth", [100_000])
def test_too_deeply_nested_json_is_400_invalid_json(client, depth):
    for body in (b"[" * depth + b"]" * depth,
                 b'[{"id":1,"raw_address":"x","deep":' + b"[" * depth + b"]" * depth + b"}]"):
        r = client.post(URL, content=body, headers={"content-type": "application/json"})
        assert r.status_code == 400 and err_code(r) == "invalid_json", f"{r.status_code}: {r.text[:120]}"


@pytest.mark.parametrize("body", [5, 1.5, True, "text", None, {"foo": [GOOD]}, {"items": "x"}, {"items": {"id": 1}}, {}], ids=repr)
def test_unknown_envelope_is_422(client, body):
    r = post(client, body)
    assert r.status_code == 422, f"{r.status_code}: {r.text[:200]}"
    assert err_code(r) == "invalid_envelope"


def test_more_than_10000_items_is_413(client):
    r = post(client, [{"id": i, "raw_address": ""} for i in range(10001)])
    assert r.status_code == 413, r.status_code


def test_exactly_10000_items_is_accepted(client):
    r = post(client, [{"id": i, "raw_address": ""} for i in range(10000)])
    assert r.status_code == 200 and len(r.json()) == 10000


# ----------------------------------------------------------------------------- limits (C2): 3000 chars, both routes
@pytest.mark.parametrize("n", [3000])
def test_raw_address_of_exactly_3000_chars_is_accepted_on_both_routes(client, n):
    raw = "ы" * n          # 3000 code points = 6000 bytes: the limit is in characters, not bytes
    body = [{"id": 1, "raw_address": raw}]
    r = post(client, body)
    assert r.status_code == 200 and r.json()[0]["raw_address"] == raw
    u = upload(client, json.dumps(body, ensure_ascii=False).encode())
    assert u.status_code == 200 and u.json()["items"][0]["raw_address"] == raw


@pytest.mark.defect("A07")
def test_raw_address_of_3001_chars_is_a_null_record_with_echo_on_parse_batch(client):
    """C3-v1.1 п.3 supersedes the v1.0 «413/422 for over-long»: the batch is processed, the item is a null-record."""
    raw = "ы" * 3001
    r = post(client, [{"id": 1, "raw_address": raw}])
    assert_200(r)
    assert_null_record(r.json()[0], 1, raw)
    assert r.headers.get(HEADER) == "1"


@pytest.mark.defect("A07")
def test_raw_address_of_3001_chars_is_a_null_record_with_echo_on_upload_too(client):
    """Limits are unified for parse-batch AND upload (C2)."""
    raw = "ы" * 3001
    u = upload(client, json.dumps([{"id": 1, "raw_address": raw}], ensure_ascii=False).encode())
    assert_200(u)
    assert list(u.json()) == ["items"] and len(u.json()["items"]) == 1
    assert_null_record(u.json()["items"][0], 1, raw)


def test_limit_counts_characters_not_utf8_bytes(client):
    raw = "ұ" * 3000  # 2 bytes each
    assert post(client, [{"id": 1, "raw_address": raw}]).status_code == 200


@pytest.mark.defect("A07")
def test_json_body_over_32_mib_is_413_before_processing(client):
    body = b'[{"id":1,"raw_address":"ul. Mira 5","pad":"' + b"a" * (32 * 1024 * 1024 + 1024) + b'"}]'
    assert len(body) > 32 * 1024 * 1024
    r = client.post(URL, content=body, headers={"content-type": "application/json"})
    assert r.status_code == 413, f"body cap missing: HTTP {r.status_code}"
    assert err_code(r) == "body_too_large"


# ----------------------------------------------------------------------------- upload
def test_upload_json_array_returns_items_envelope(client):
    u = upload(client, json.dumps([GOOD], ensure_ascii=False).encode())
    assert u.status_code == 200 and list(u.json()) == ["items"] and u.json()["items"][0]["house"] == "5"


def test_upload_wrapper_returns_items_envelope(client):
    u = upload(client, json.dumps({"records": [GOOD]}).encode())
    assert u.status_code == 200 and list(u.json()) == ["items"]


def test_upload_with_utf8_bom(client):
    u = upload(client, b"\xef\xbb\xbf" + json.dumps([GOOD], ensure_ascii=False).encode("utf-8"))
    assert u.status_code == 200 and u.json()["items"][0]["id"] == "g1"


def test_upload_jsonl_with_blank_lines(client):
    lines = [json.dumps({"id": i, "raw_address": f"ул. Мира {i + 1}"}, ensure_ascii=False) for i in range(3)]
    data = ("\n\n".join(lines) + "\n\n").encode()
    u = upload(client, data, "batch.jsonl")
    assert u.status_code == 200
    assert [x["house"] for x in u.json()["items"]] == ["1", "2", "3"]


@pytest.mark.defect("C7-JSONL")
def test_upload_jsonl_malformed_line_is_400_with_line_index_and_no_echo(client):
    lines = [json.dumps(GOOD), json.dumps(GOOD), '{"id": 3, "raw_address": "' + SENTINEL, json.dumps(GOOD)]
    u = upload(client, "\n".join(lines).encode(), "batch.jsonl")
    assert u.status_code == 400, f"{u.status_code}: {u.text[:200]}"
    assert err_code(u) == "invalid_jsonl"
    assert SENTINEL not in u.text
    try:
        nums = ints_in(u.json())
    except ValueError:
        nums = []
    assert {2, 3} & set(nums), f"line index (0- or 1-based) must be reported, got body {u.text[:200]}"
    assert "line" in u.json()["detail"], "the contract names the field `line`"


@pytest.mark.parametrize("data", [b"\xff\xfe\x00bad", b"{", b""])
def test_upload_garbage_is_400(client, data):
    assert upload(client, data).status_code == 400


@pytest.mark.parametrize("data", [b"{", b"not json"])
def test_upload_broken_json_is_400_invalid_json(client, data):
    u = upload(client, data)
    assert u.status_code == 400 and err_code(u) == "invalid_json"


def test_upload_too_deeply_nested_json_is_400_invalid_json(client):
    u = upload(client, b"[" * 100_000 + b"]" * 100_000)
    assert u.status_code == 400 and err_code(u) == "invalid_json"


@pytest.mark.parametrize("data", [b"5", b'"text"', b"null", b'{"foo": [1]}', b"{}"])
def test_upload_unknown_envelope_is_422(client, data):
    u = upload(client, data)
    assert u.status_code == 422 and err_code(u) == "invalid_envelope", f"{u.status_code}: {u.text[:120]}"


MIB8 = 8 * 1024 * 1024


def _padded_json(total: int) -> bytes:
    return b"[]" + b" " * (total - 2)


def test_upload_just_below_8_mib_is_accepted(client):
    """8 MiB minus 1 KiB of file content must pass (leaves room for multipart overhead)."""
    assert upload(client, _padded_json(MIB8 - 1024)).status_code == 200


@pytest.mark.soft
def test_upload_of_exactly_8_mib_file_is_accepted(client):
    """C2 says «upload ≤ 8 MiB». If the cap is applied to the whole request (Content-Length incl. multipart envelope)
    a file of exactly 8 MiB is rejected; the contract does not say file-vs-request, hence soft."""
    r = upload(client, _padded_json(MIB8))
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:120]}"


def test_upload_above_8_mib_is_413(client):
    assert upload(client, _padded_json(MIB8 + 1)).status_code == 413


# --- upload with invalid items (C3-v1.1 applies to upload JSON and JSONL as well) -----------------------------------
@pytest.mark.defect("C3-v1.1")
def test_upload_json_array_with_invalid_items_returns_one_object_per_element(client):
    long_raw = "ы" * 3001
    u = upload(client, json.dumps([GOOD, {"id": 1, "raw_address": 34}, 5, None, {"id": "x", "raw_address": long_raw},
                                   {"id": 1.5, "raw_address": "ул. Абая 7"}], ensure_ascii=False).encode())
    assert_200(u)
    assert list(u.json()) == ["items"]
    out = u.json()["items"]
    assert len(out) == 6 and out[0]["id"] == "g1" and out[0]["house"] == "5"
    assert_null_record(out[1], 1)
    assert_null_record(out[2], None)
    assert_null_record(out[3], None)
    assert_null_record(out[4], "x", long_raw)
    assert out[5]["id"] is None and out[5]["house"] == "7" and out[5]["raw_address"] == "ул. Абая 7"
    no_item_error_leak(u.text)


@pytest.mark.defect("C3-v1.1")
@pytest.mark.parametrize("key", ["items", "records", "addresses"])
def test_upload_wrapper_with_invalid_items_returns_items_envelope(client, key):
    u = upload(client, json.dumps({key: [OK1, "строка", {"id": 2}, OK2]}, ensure_ascii=False).encode())
    assert_200(u)
    assert list(u.json()) == ["items"] and len(u.json()["items"]) == 4
    out = u.json()["items"]
    assert out[0]["house"] == "5" and out[3]["house"] == "7"
    assert_null_record(out[1], None)
    assert_null_record(out[2], 2)


@pytest.mark.defect("C3-v1.1")
@pytest.mark.parametrize("single,expect_null", [({"id": 5, "raw_address": 34}, True), (OK1, False)], ids=["bad", "good"])
def test_upload_single_record_gives_items_with_exactly_one_object(client, single, expect_null):
    u = upload(client, json.dumps(single).encode())
    assert_200(u)
    assert list(u.json()) == ["items"] and len(u.json()["items"]) == 1
    rec = u.json()["items"][0]
    if expect_null:
        assert_null_record(rec, 5)
    else:
        assert rec["id"] == "ok1" and rec["house"] == "5"


@pytest.mark.defect("C3-v1.1")
def test_upload_jsonl_with_valid_json_but_invalid_items_gives_one_object_per_nonblank_line(client):
    long_raw = "ы" * 3001
    lines = [json.dumps(OK1), "5", "null", json.dumps("строка", ensure_ascii=False), "[1, 2]", '{"id": 3}',
             '{"id": 4, "raw_address": 34}', json.dumps({"id": 5, "raw_address": long_raw}), '{"id": 6, "raw_address": "\\ud800"}',
             "", "   ", json.dumps(OK2), '{"id": 1.5, "raw_address": "ул. Мира 5"}']
    u = upload(client, "\n".join(lines).encode(), "batch.jsonl")
    assert_200(u)
    out = u.json()["items"]
    assert len(out) == 11, "one object per NON-BLANK line; blank lines are skipped"
    assert out[0]["house"] == "5" and out[9]["house"] == "7"
    for idx, rid in ((1, None), (2, None), (3, None), (4, None), (5, 3), (6, 4), (8, 6)):
        assert_null_record(out[idx], rid)
    assert_null_record(out[7], 5, long_raw)
    assert out[10]["id"] is None and out[10]["house"] == "5"


@pytest.mark.defect("C3-v1.1")
def test_upload_jsonl_malformed_line_still_rejects_the_whole_file_even_with_invalid_items_around(client):
    lines = ["5", json.dumps(OK1), '{"id": 3, "raw_address": "' + SENTINEL, "null"]
    u = upload(client, "\n".join(lines).encode(), "batch.jsonl")
    assert u.status_code == 400 and err_code(u) == "invalid_jsonl" and SENTINEL not in u.text


@pytest.mark.soft
def test_upload_also_sets_the_invalid_items_header(client):
    """п.5 speaks about «заголовок ответа» in general; applying it to upload is a QA inference (hence soft)."""
    u = upload(client, json.dumps([OK1, 5, {"id": 1}]).encode())
    assert_200(u)
    assert u.headers.get(HEADER) == "2"
    clean = upload(client, json.dumps([OK1, OK2]).encode())
    assert_200(clean)
    assert clean.headers.get(HEADER) is None


# ----------------------------------------------------------------------------- health / error_count (C7)
@pytest.mark.defect("C7-HEALTH")
def test_health_exposes_error_count_that_moves_on_internal_failure(client, monkeypatch):
    """C7: the difference between a system error and an ordinary unconfident parse is visible in /health.error_count."""
    before = client.get("/health").json()
    assert "error_count" in before, "additive /health.error_count missing (C7)"
    import app.parser as parser
    real = parser.parse_address
    monkeypatch.setattr(parser, "parse_address", lambda rec: (_ for _ in ()).throw(RuntimeError("synthetic")) if "BOOM" in rec["raw_address"] else real(rec))
    r = post(client, [{"id": 1, "raw_address": "BOOM ул. Мира 5"}, {"id": 2, "raw_address": "ул. Мира 5"}])
    assert r.status_code == 500 and r.json() == {"detail": {"code": "internal_error"}}
    assert r.headers.get(HEADER) is None
    after = client.get("/health").json()
    assert after["error_count"] == before["error_count"] + 1


# ----------------------------------------------------------------------------- real HTTP (uvicorn)
@pytest.mark.rest
@pytest.mark.defect("C3-v1.1")
def test_rest_mixed_batch_over_real_http_has_header_and_is_idempotent():
    """The header must survive a real server stack and the answer must be byte-identical across a restart."""
    import urllib.error
    import urllib.request

    from .server import running_server
    body = json.dumps(_mixed_body(), ensure_ascii=False).encode("utf-8")
    seen = []
    for _ in range(2):                                   # two separate server processes
        with running_server() as base:
            req = urllib.request.Request(f"{base}{URL}", data=body, headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    seen.append((resp.status, resp.headers.get(HEADER), resp.read()))
            except urllib.error.HTTPError as e:
                raise AssertionError(f"item-level handling expected HTTP 200, got {e.code}: {e.read()[:200]!r}") from None
    assert seen[0] == seen[1], "restart changed the answer"
    status, header, raw = seen[0]
    out = json.loads(raw)
    assert status == 200 and header == "5" and len(out) == 10
    assert_null_record(out[1], None)
    assert_null_record(out[2], 2)
    assert_null_record(out[4], 3, "ы" * 3001)
