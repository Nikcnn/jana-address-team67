"""Portable integrity/provenance checks; no external calls or private data."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = json.loads((ROOT / "data/gazetteer.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((ROOT / "data/geo/MANIFEST.json").read_text(encoding="utf-8"))
TARGETS = {"Аксай 3", "Аксай 3а"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_two_targets_have_independent_osm_provenance_and_consistent_license():
    entries = {r["name"]: r for r in DATA["living_areas"] if r["name"] in TARGETS}
    assert set(entries) == TARGETS
    for record in entries.values():
        assert record["source"] == "osm-aksay-targets"
        assert "independently acquired 2026-10-18" in record["provenance"]
        assert "historical illustrative seed" in record["provenance"].lower()
        assert "representative-point" in record["provenance"]
        assert "not whole-polygon containment" in record["provenance"]
    assert "jana-sample" not in DATA["sources"]
    table = MANIFEST["tables"]["living_areas"]
    assert table["source"] == ["osm-aksay-targets", "osm-check"]
    assert table["redistribution"] == "allowed_with_attribution_and_share_alike"
    assert "ODbL 1.0" in table["license"]
    assert table["records"] == len(DATA["living_areas"])
    assert table["sha256"] == digest(DATA["living_areas"])
    source = DATA["sources"]["osm-aksay-targets"]
    assert source["redistribution"] == table["redistribution"]
    assert source["license"] == "ODbL 1.0"
    assert MANIFEST["sources"]["osm-aksay-targets"]["retrieved_at"] == source["retrieved_at"]


def test_non_target_records_and_sources_remain_identical_to_frozen_base():
    # Computed from verified baseline4b9879d5...; no absolute workspace dependency.
    clean = dict(DATA)
    clean["living_areas"] = [r for r in DATA["living_areas"] if r["name"] not in TARGETS]
    clean["sources"] = {k: v for k, v in DATA["sources"].items() if k != "osm-aksay-targets"}
    assert digest(clean) == "75088f09b103fba6161d4141b4f601c3a2f5019ac207a9ad4b71429684d0bd9e"


def test_target_runtime_names_aliases_and_associations_unchanged():
    stable = [{k: v for k, v in r.items() if k not in {"source", "provenance"}}
              for r in DATA["living_areas"] if r["name"] in TARGETS]
    assert digest(stable) == "58c913362318e5100b8e17be2a1826caa262491ede1d86e78761571d7038d168"


def test_runtime_names_aliases_resolve_unchanged():
    from app.geo import resolve_area
    for name, alias in [("Аксай 3", "Аксай-3"), ("Аксай 3а", "Аксай-3а")]:
        assert resolve_area(name, "Алматы")["name"] == name
        assert resolve_area(alias, "Алматы")["name"] == name


def test_saved_entity_and_admin_evidence_has_exact_ids_and_bounded_queries():
    snapshot = json.loads((ROOT / "docs/geo-provenance/aksay-osm-snapshot.json").read_text(encoding="utf-8"))
    entities = {e["id"]: e for e in snapshot["elements"]}
    assert set(entities) == {265577783, 265577786}
    for ident, version, ru in [(265577783, 11, "Аксай 3А"), (265577786, 16, "Аксай 3")]:
        assert entities[ident]["type"] == "way"
        assert entities[ident]["version"] == version
        assert entities[ident]["tags"]["name:ru"] == ru
        assert entities[ident]["tags"]["place"] == "suburb"
    assert "[timeout:20]" in snapshot["fetch"]["query"]
    assert "(43.10,76.60,43.45,77.20)" in snapshot["fetch"]["query"]
    assert "filesystem mtime" in snapshot["fetch"]["retrieved_at_method"]
    assert snapshot["fetch"]["retrieved_at"] != snapshot["osm3s"]["timestamp_osm_base"]
    assert DATA["sources"]["osm-aksay-targets"]["retrieved_at"] == snapshot["fetch"]["retrieved_at"]
    for check in snapshot["admin_point_checks"]:
        assert check["way_id"] in entities
        assert "not proof of whole polygon containment" in check["method"]
        assert "[timeout:20]" in check["query"]
        assert datetime.fromisoformat(check["fetched_at"]).tzinfo is not None
        by_id = {x["id"]: x for x in check["entities"]}
        assert by_id[3602465058]["tags"]["name"] == "Алматы"
        assert by_id[3603072808]["tags"]["name:ru"] == "Ауэзовский район"
    assert {x["way_id"] for x in snapshot["admin_point_checks"]} == set(entities)


def test_notice_and_manifest_state_conditional_odbl_attribution():
    notice = (ROOT / "NOTICE.md").read_text(encoding="utf-8")
    assert "way/265577783" in notice and "way/265577786" in notice
    assert "conditional on attribution and share-alike" in notice
    assert "Ауэзовский" in notice and "representative-point" in notice
    assert "not whole-polygon containment" in notice
