"""Acceptance suite §4 (scenarios 1-11 + W1 extras) — positive AND negative assertions per field.

Expectations live in data/qa/acceptance_scenarios.json (taken from the task text / contract, never from parser output).
Each assertion is a separate test id, so a RED baseline shows exactly which field is wrong.
"""
from __future__ import annotations

import functools

import pytest

from .helpers import LOW_CONFIDENCE, COMPONENTS, parse_one, scenarios

ALL = scenarios()
CONTRACT = [s for s in ALL if s["status"] == "contract"]


@functools.lru_cache(maxsize=None)
def out(raw: str) -> dict:
    return parse_one(raw)


def _cases():
    for sc in CONTRACT:
        sid, raw = sc["id"], sc["raw"]
        for field, want in sc.get("expect", {}).items():
            yield pytest.param(sid, raw, "expect", field, want, id=f"{sid}-expect-{field}")
        for field, want in sc.get("expect_derived", {}).items():
            yield pytest.param(sid, raw, "expect", field, want, id=f"{sid}-derived-{field}", marks=pytest.mark.soft)
        for field in sc.get("expect_null", []):
            yield pytest.param(sid, raw, "null", field, None, id=f"{sid}-null-{field}")
        for field, allowed in sc.get("one_of", {}).items():
            yield pytest.param(sid, raw, "one_of", field, allowed, id=f"{sid}-oneof-{field}")
        for field, banned in sc.get("not_equal", {}).items():
            yield pytest.param(sid, raw, "not_equal", field, banned, id=f"{sid}-neg-notequal-{field}")
        for field, parts in sc.get("not_contains", {}).items():
            yield pytest.param(sid, raw, "not_contains", field, parts, id=f"{sid}-neg-notcontains-{field}")
        for field, parts in sc.get("contains", {}).items():
            yield pytest.param(sid, raw, "contains", field, parts, id=f"{sid}-contains-{field}")
        if "conf_max" in sc:
            yield pytest.param(sid, raw, "conf_max", "confidence", sc["conf_max"], id=f"{sid}-conf-max")
        for part in sc.get("no_field_contains", []):
            yield pytest.param(sid, raw, "no_field_contains", "*", part, id=f"{sid}-neg-nofield-{part}")


@pytest.mark.parametrize("sid,raw,kind,field,arg", list(_cases()))
def test_acceptance(sid, raw, kind, field, arg):
    r = out(raw)
    got = r.get(field) if field in r else None
    ctx = f"\n  scenario {sid}: raw={raw!r}\n  parsed={ {k: v for k, v in r.items() if v is not None and k not in ('id', 'raw_address')} }"
    if kind == "expect":
        assert got == arg, f"{field}: expected {arg!r}, got {got!r}{ctx}"
    elif kind == "null":
        assert got is None, f"{field}: expected null (no fabrication), got {got!r}{ctx}"
    elif kind == "one_of":
        assert got in arg, f"{field}: expected one of {arg!r}, got {got!r}{ctx}"
    elif kind == "not_equal":
        assert got not in arg, f"{field}: must not be any of {arg!r}, got {got!r}{ctx}"
    elif kind == "not_contains":
        if got is not None:
            hits = [p for p in arg if p.lower() in got.lower()]
            assert not hits, f"{field}={got!r} leaks marker/detail text {hits!r}{ctx}"
    elif kind == "contains":
        assert got is not None and all(p in got for p in arg), f"{field}: expected to contain {arg!r}, got {got!r}{ctx}"
    elif kind == "conf_max":
        assert r["confidence"] <= arg, f"confidence {r['confidence']} > {arg} (no confident answer from a bare POI){ctx}"
    elif kind == "no_field_contains":
        hits = [k for k in COMPONENTS if isinstance(r[k], str) and arg.lower() in r[k].lower()]
        assert not hits, f"text {arg!r} must not appear in any component, found in {hits}{ctx}"


# --- P4: adjudication-pending (gold NOT fabricated) -------------------------------------------------------
P4 = next(s for s in ALL if s["id"] == "P4")


@pytest.mark.parametrize("field,want", sorted(P4["minimal_proven"].items()), ids=lambda v: str(v))
def test_p4_minimal_proven_fields(field, want):
    """Only the fields carrying explicit markers («дом 5», «кв 8») and the explicit city are asserted."""
    r = out(P4["raw"])
    assert r[field] == want, f"{field}: expected {want!r}, got {r[field]!r}; parsed={r}"


def test_p4_street_name_does_not_swallow_markers():
    r = out(P4["raw"])
    name = r["street_name"]
    if name is not None:
        assert not any(p in name.lower() for p in P4["not_contains"]["street_name"]), f"street_name={name!r}"


@pytest.mark.pending
@pytest.mark.skip(reason="adjudication-pending: micro-district numbering «15 мкр» vs «22 дом»; "
                         "street_type/street_name gold intentionally NOT fabricated (see ANNOTATION_POLICY §5)")
def test_p4_microdistrict_numbering_pending():
    raise AssertionError("never executed")


# --- sanity of the scenario file itself ------------------------------------------------------------------
def test_scenarios_file_is_well_formed():
    ids = [s["id"] for s in ALL]
    assert len(ids) == len(set(ids))
    for s in ALL:
        keys = set(s.get("expect", {})) | set(s.get("expect_derived", {})) | set(s.get("expect_null", [])) \
               | set(s.get("one_of", {})) | set(s.get("not_equal", {})) | set(s.get("not_contains", {}))
        assert keys <= set(COMPONENTS), f"{s['id']}: unknown fields {keys - set(COMPONENTS)}"
        assert set(s.get("expect", {})).isdisjoint(s.get("expect_null", [])), f"{s['id']}: field both expected and null"


def test_every_master_prompt_scenario_is_covered():
    """Master prompt §4 items 1-11 must each have a scenario id (12/13 are covered by test_negative/test_poi_inference)."""
    have = {s["id"] for s in ALL}
    assert {f"S{n:02d}" for n in range(1, 11)} <= have
    assert {"S11a", "S11b", "S11c", "P4"} <= have
