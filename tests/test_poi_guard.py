"""POI guard (integrator-owned, owner decision 2026-10-18).

Rule: NEVER fill street/house/district/apartment/... from a POI record's
`linked_address` (the shipped POI rows are SYNTHETIC fixtures). A store name
without a proven unique link must not produce an invented exact address, and
even a unique synthetic branch must not leak its fixture address into the
14-field output. Unconfirmed address fields stay null.
"""
import ast
import json
import pathlib

import pytest

from app.parser import parse_address

ROOT = pathlib.Path(__file__).resolve().parent.parent
POI = json.loads((ROOT / "data" / "gazetteer.json").read_text(encoding="utf-8"))["poi"]
ADDRESS_FIELDS = ("street_type", "street_name", "house", "apartment", "entrance", "floor", "district", "living_area")


def parse(raw):
    return parse_address({"id": "poi-guard", "raw_address": raw})


def test_every_shipped_poi_is_marked_synthetic():
    assert POI, "gazetteer has no POI table"
    for row in POI:
        assert row.get("synthetic") is True, row.get("branch_id")


@pytest.mark.parametrize("raw", [
    "магазин Береке",                      # ambiguous: 3 synthetic branches, no city
    "Береке",
    "магазин Bereke",
    "магазин Береке, Алматы",              # unique synthetic branch in that city
    "магазин Береке, Шымкент",
    "магазин Береке Синтетикоград",
    "Аптека Синтетика",                    # unique synthetic name, no city
    "Аптека Синтетика, Алматы",
    "магазин НесуществующийМагазин, Алматы",   # unknown store
])
def test_poi_names_never_fill_address_fields(raw):
    out = parse(raw)
    assert all(out[f] is None for f in ADDRESS_FIELDS), (raw, {f: out[f] for f in ADDRESS_FIELDS})


def test_unique_poi_does_not_leak_linked_address():
    out = parse("магазин Береке, Алматы")
    blob = " ".join(str(v) for k, v in out.items() if k not in ("id", "raw_address") and v is not None)
    # fixture address is "мкр Аксай 3, 12": none of its parts may appear in outputs
    assert "Аксай" not in blob and out["house"] != "12"
    assert out["landmark"] is not None and "Береке" in out["landmark"]


def test_ambiguous_poi_without_city_has_no_geography():
    out = parse("магазин Береке")
    assert out["city"] is None and out["province"] is None and out["district"] is None
    assert out["landmark"] == "магазин Береке"


def test_runtime_parser_and_api_never_call_poi_lookup():
    for rel in ("app/parser.py", "app/main.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
                {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | \
                {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        assert not ({"lookup_poi", "lookup_poi_unique"} & names), rel
