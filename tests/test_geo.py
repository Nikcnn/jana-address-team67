"""Contract tests for the offline geographic reference (W2).

Covers: schema compatibility (C6), RU/KZ/Latin alias matching, whole-token boundaries,
street-context rejection, unique-only resolution of duplicated names, living areas, city
districts, POI lookup, provenance/licensing manifest integrity and the offline-runtime guarantee.
"""
import copy
import hashlib
import json
import re
from pathlib import Path

import app.geo as geo
from app.geo import (
    city_for_district,
    find_city,
    find_province,
    get_data,
    lookup_poi,
    lookup_poi_unique,
    resolve_area,
)

ROOT = Path(__file__).resolve().parents[1]
GAZ = json.loads((ROOT / "data" / "gazetteer.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((ROOT / "data" / "geo" / "MANIFEST.json").read_text(encoding="utf-8"))

BASELINE_CITIES = {
    "Алматы": "Алматы", "Астана": "Астана", "Шымкент": "Шымкент",
    "Актобе": "Актюбинская область", "Караганда": "Карагандинская область",
    "Павлодар": "Павлодарская область", "Семей": "Абайская область",
    "Усть-Каменогорск": "Восточно-Казахстанская область", "Тараз": "Жамбылская область",
    "Кызылорда": "Кызылординская область", "Атырау": "Атырауская область",
    "Актау": "Мангистауская область", "Костанай": "Костанайская область",
    "Кокшетау": "Акмолинская область", "Петропавловск": "Северо-Казахстанская область",
    "Талдыкорган": "Жетысуская область", "Туркестан": "Туркестанская область",
    "Уральск": "Западно-Казахстанская область", "Жезказган": "Улытауская область",
    "Экибастуз": "Павлодарская область", "Боралдай": "Алматинская область",
}

OBLASTS = {
    "Алматинская область", "Актюбинская область", "Карагандинская область",
    "Акмолинская область", "Абайская область", "Восточно-Казахстанская область",
    "Жамбылская область", "Кызылординская область", "Атырауская область",
    "Мангистауская область", "Костанайская область", "Северо-Казахстанская область",
    "Жетысуская область", "Туркестанская область", "Западно-Казахстанская область",
    "Улытауская область", "Павлодарская область",
}


def table_sha(table) -> str:
    raw = json.dumps(table, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def cities_by_name():
    return {c["name"]: c for c in GAZ["cities"]}


# ---------------------------------------------------------------- schema / additive


def test_contract_keys_and_types_preserved():
    assert isinstance(get_data(), dict)
    for key in ("_meta", "cities", "provinces", "living_areas"):
        assert key in GAZ, key
    assert isinstance(GAZ["provinces"], list) and all(isinstance(p, str) for p in GAZ["provinces"])
    assert isinstance(GAZ["cities"], list) and isinstance(GAZ["living_areas"], list)
    for key in ("districts", "province_aliases", "poi", "sources"):
        assert isinstance(GAZ[key], list if key != "sources" else dict), key


def test_meta_declares_source_license_limitations():
    meta = GAZ["_meta"]
    for key in ("source", "license", "limitations"):
        assert isinstance(meta[key], str) and meta[key].strip(), key
    assert "not" in meta["limitations"].lower() or "не" in meta["limitations"].lower()


def test_baseline_cities_and_provinces_unchanged():
    by_name = cities_by_name()
    for name, province in BASELINE_CITIES.items():
        assert name in by_name, f"legacy city lost: {name}"
        assert by_name[name]["province"] == province, name
    living = {a["name"] for a in GAZ["living_areas"]}
    assert {"Аксай 3а", "Аксай 3"} <= living


def test_all_provinces_are_17_oblasts_plus_3_republican_cities():
    assert len(GAZ["provinces"]) == 20
    assert set(GAZ["provinces"]) == OBLASTS | {"Алматы", "Астана", "Шымкент"}
    for city in GAZ["cities"]:
        assert city["province"] in GAZ["provinces"], city["name"]


def test_city_coverage_is_national_by_name():
    assert len(GAZ["cities"]) >= 90
    assert len({c["name"] for c in GAZ["cities"]}) == len(GAZ["cities"])
    # every oblast has at least one city mapped to it
    provinces_used = {c["province"] for c in GAZ["cities"]}
    assert provinces_used == set(GAZ["provinces"])


def test_every_record_has_provenance_and_resolvable_source():
    groups = [GAZ["cities"], GAZ["districts"], GAZ["living_areas"], GAZ["poi"], GAZ["province_aliases"]]
    for group in groups:
        for record in group:
            assert isinstance(record.get("provenance"), str) and record["provenance"].strip(), record
            assert record.get("source") in GAZ["sources"], record.get("source")


def test_no_keys_derived_from_id_or_raw_address():
    forbidden = {"id", "raw_address", "raw", "raw_hash", "address_hash", "hash", "answer"}

    def walk(node, path="$"):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key.casefold() not in forbidden, f"forbidden key {path}.{key}"
                assert "raw_address" not in key.casefold(), f"forbidden key {path}.{key}"
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    walk(GAZ)


# ---------------------------------------------------------------- aliases


def test_city_aliases_cover_ru_kz_and_latin():
    by_name = cities_by_name()
    assert "Қарағанды" in by_name["Караганда"]["aliases"]
    assert "Karaganda" in by_name["Караганда"]["aliases"]
    assert "Өскемен" in by_name["Усть-Каменогорск"]["aliases"]
    assert "Oskemen" in by_name["Усть-Каменогорск"]["aliases"]
    assert "Орал" in by_name["Уральск"]["aliases"]
    assert "Petropavl" in by_name["Петропавловск"]["aliases"]
    assert "Құлсары" in by_name["Кульсары"]["aliases"]
    for city in GAZ["cities"]:
        assert city["name"] in city["aliases"]
        assert any(a.isascii() and a != city["name"] for a in city["aliases"]), city["name"]


def test_city_aliases_are_globally_unique():
    owner = {}
    for city in GAZ["cities"]:
        for alias in city["aliases"]:
            key = alias.casefold()
            assert key not in owner, f"alias {alias!r} shared by {owner.get(key)} and {city['name']}"
            owner[key] = city["name"]


def test_every_alias_resolves_to_its_city():
    for city in GAZ["cities"]:
        for alias in city["aliases"]:
            found, span = find_city(alias)
            assert found is not None, f"{city['name']}: alias {alias!r} not found"
            assert found["name"] == city["name"], f"{alias!r} -> {found['name']}"
            assert span[1] > span[0]


# ---------------------------------------------------------------- boundary behaviour


def test_almaty_is_not_almaty_oblast():
    assert find_province("Алматы") == (None, None)
    province, _ = find_province("Алматинская область")
    assert province == "Алматинская область"
    assert find_city("Алматинская область") == (None, None)
    assert find_city("Алматинская") == (None, None)


def test_almaty_region_alias_is_not_almaty_city():
    for text in ("Алматы облысы", "Алматы область", "Almaty Region", "Almaty Oblast"):
        assert find_city(text) == (None, None), text


def test_district_named_astana_is_not_the_capital():
    assert find_city("район Астана") == (None, None)
    assert find_city("Астана ауданы") == (None, None)
    assert find_city("Астана")[0]["name"] == "Астана"
    assert find_city("г. Астана")[0]["name"] == "Астана"


def test_province_matching_ru_and_kz():
    for alias, canonical, span_len in (
        ("Алматинская область", "Алматинская область", len("Алматинская область")),
        ("Алматы облысы", "Алматинская область", len("Алматы облысы")),
        ("Қарағанды облысы", "Карагандинская область", len("Қарағанды облысы")),
        ("Павлодар облысы", "Павлодарская область", len("Павлодар облысы")),
    ):
        province, span = find_province(alias)
        assert province == canonical, alias
        assert span[1] - span[0] == span_len
    assert find_province("Алматинская") == (None, None)


def test_province_aliases_cover_all_oblasts_with_kz_form():
    entries = {e["name"]: e for e in GAZ["province_aliases"]}
    assert set(entries) == set(GAZ["provinces"])
    for oblast in OBLASTS:
        assert any(a.endswith("облысы") for a in entries[oblast]["aliases"]), oblast
        assert find_province(next(a for a in entries[oblast]["aliases"] if a.endswith("облысы")))[0] == oblast


# ---------------------------------------------------------------- street context


def test_city_alias_in_street_context_is_not_a_city():
    # W1/W2 integration conflict: 'Абай' is both a city and a street name.
    for text in (
        "Абай көшесі",
        "Абай көшесі 13Б үй 2-пәтер 4-кіреберіс 9-қабат",
        "Абай көше",
        "Абай даңғылы 13Б",
        "Абай көшесі, 13Б үй",
        "ул. Абай 5",
        "проспект Абай 12",
        "Аксай көшесі 3",
        "Abay st 5, kv 2",
        "st Abay 5 flat 2",
        "st. Abay 5",
        "Abay Street 5",
        "Avenue Abay 5",
    ):
        assert find_city(text) == (None, None), text


def test_city_without_street_marker_is_still_found():
    for text, name in (
        ("Абай", "Абай"),
        ("г. Абай", "Абай"),
        ("Абай қаласы", "Абай"),
        ("Аксай", "Аксай"),
        ("Aksay", "Аксай"),
        # the marker belongs to the next street, the alias stays a real city
        ("Алматы улица Мира дом 10", "Алматы"),
        ("г Семей ул Мира д 3", "Семей"),
        ("Алматы қаласы, Абай даңғылы, 13Б үй, 2-пәтер", "Алматы"),
        ("Алматы, Ауэзовский район, Абай көшесі 89/1", "Алматы"),
        ("Тараз Жамбыла 100/2 кв.4 3-этаж", "Тараз"),
        ("Актау 15 мкр 22 дом 5 кв 8", "Актау"),
        ("Астана Кабанбай батыра 33/1", "Астана"),
    ):
        found, span = find_city(text)
        assert found is not None and found["name"] == name, text
        assert span[1] > span[0]


def test_microdistrict_prefix_is_not_a_city():
    # 'Мкр Аксай 3а' is the Алматы living area, not the city of Аксай.
    assert find_city("Мкр Аксай 3а") == (None, None)
    assert find_city("мкр Аксай 3а, 59, кв 70") == (None, None)
    assert find_city("Алматы, Мкр Аксай 3а, 59, кв 70")[0]["name"] == "Алматы"
    assert find_city("город Алматы/Алматы қаласы, Мкр Аксай 3а, 59, кв 70")[0]["name"] == "Алматы"


# ---------------------------------------------------------------- living areas


def test_resolve_area_exact_and_unique():
    area = resolve_area("Аксай 3а", "Алматы")
    assert area is not None and area["name"] == "Аксай 3а" and area["district"] == "Ауэзовский"
    assert resolve_area("Аксай-3а", "Алматы")["name"] == "Аксай 3а"
    samal = resolve_area("Самал 2", "Алматы")
    assert samal is not None and samal["district"] == "Бостандыкский"
    assert resolve_area("самал-2", "алматы")["name"] == "Самал 2"
    assert resolve_area("Самал 2", "Астана") is None
    assert resolve_area("Несуществующий микрорайон", "Алматы") is None
    assert resolve_area("", "Алматы") is None and resolve_area(None, None) is None


def test_resolve_area_all_aliases_resolve_within_city():
    for area in GAZ["living_areas"]:
        for alias in area["aliases"]:
            found = resolve_area(alias, area["city"])
            assert found is not None and found["name"] == area["name"], alias


def test_resolve_area_duplicate_names_are_unique_only(monkeypatch):
    data = copy.deepcopy(GAZ)
    duplicate = copy.deepcopy(next(a for a in data["living_areas"] if a["name"] == "Самал 1"))
    duplicate["city"] = "Астана"
    duplicate["district"] = None
    data["living_areas"].append(duplicate)
    monkeypatch.setattr(geo, "get_data", lambda: data)
    assert resolve_area("Самал 1", None) is None  # ambiguous without city
    assert resolve_area("Самал 1", "Алматы")["city"] == "Алматы"
    assert resolve_area("Самал 1", "Астана")["city"] == "Астана"


def test_living_area_links_are_consistent():
    by_name = cities_by_name()
    for area in GAZ["living_areas"]:
        assert area["city"] in by_name, area["name"]
        if area.get("district"):
            assert area["district"] in by_name[area["city"]].get("districts", []), area["name"]


# ---------------------------------------------------------------- districts


def test_city_for_district_known_cases():
    assert city_for_district("Ауэзовский")["name"] == "Алматы"
    assert city_for_district("Ауэзовский район")["name"] == "Алматы"
    assert city_for_district("Бостандыкский")["name"] == "Алматы"
    assert city_for_district("Казыбекбийский")["name"] == "Караганда"
    assert city_for_district("им. Казыбек би")["name"] == "Караганда"
    assert city_for_district("Әлихан Бөкейханов ауданы")["name"] == "Караганда"
    assert city_for_district("Алматинский")["name"] == "Астана"
    assert city_for_district("Абайский")["name"] == "Шымкент"
    assert city_for_district("Несуществующий") is None
    assert city_for_district("") is None


def test_district_table_matches_city_district_lists():
    declared = {}
    for record in GAZ["districts"]:
        declared.setdefault(record["city"], set()).add(record["name"])
    for city in GAZ["cities"]:
        assert declared.get(city["name"], set()) == set(city.get("districts", [])), city["name"]
    assert len(GAZ["districts"]) == 22


def test_district_name_collisions_are_unique_only():
    # Aktobe has a district literally named 'Алматы'; Astana's Алматинский district carries
    # the Kazakh alias 'Алматы ауданы'. The resolver therefore refuses the ambiguous name...
    assert city_for_district("Алматы") is None
    # ...but the unambiguous forms still resolve, and the city table is never affected.
    assert city_for_district("Алматинский")["name"] == "Астана"
    assert find_city("Алматы")[0]["name"] == "Алматы"


def test_city_for_district_is_unique_only(monkeypatch):
    data = copy.deepcopy(GAZ)
    duplicate = copy.deepcopy(next(d for d in data["districts"] if d["name"] == "Ауэзовский"))
    duplicate["city"] = "Шымкент"
    data["districts"].append(duplicate)
    monkeypatch.setattr(geo, "get_data", lambda: data)
    assert city_for_district("Ауэзовский") is None


# ---------------------------------------------------------------- POI


def test_lookup_poi_ambiguous_missing_and_scoped():
    all_branches = lookup_poi("Береке")
    assert len(all_branches) == 3
    assert {b["branch_id"] for b in all_branches} == {"BKR-ALM-01", "BKR-SHM-01", "BKR-SYN-01"}
    assert all(b.get("synthetic") is True for b in all_branches)
    assert lookup_poi_unique("Береке") is None
    almaty = lookup_poi("Береке", city="Алматы")
    assert len(almaty) == 1 and almaty[0]["branch_id"] == "BKR-ALM-01"
    assert lookup_poi("Береке", city="Almaty")[0]["branch_id"] == "BKR-ALM-01"
    assert lookup_poi("Береке", city="Астана") == []
    assert lookup_poi("Несуществующий магазин") == []
    assert lookup_poi("Магазин Фантом", city="Алматы") == []
    assert lookup_poi("") == [] and lookup_poi(None) == []


def test_lookup_poi_alias_and_case_insensitive():
    for query in ("береке", "БЕРЕКЕ", "магазин Береке", "Bereke"):
        assert len(lookup_poi(query)) == 3, query
    unique = lookup_poi_unique("аптека синтетика")
    assert unique is not None and unique["branch_id"] == "SYN-PH-01"


def test_lookup_poi_never_matches_by_address():
    assert lookup_poi("мкр Аксай 3, 12") == []
    assert lookup_poi("мкр Аксай 3, дом 12", city="Алматы") == []
    assert lookup_poi("Аксай 3") == []  # living area, not a POI entity


def test_poi_records_have_required_fields():
    required = {"name", "aliases", "type", "city", "district", "branch_id", "linked_address", "provenance"}
    for poi in GAZ["poi"]:
        assert required <= set(poi), poi
        assert poi["synthetic"] is True
        assert isinstance(poi["branch_id"], str) and poi["branch_id"]


# ---------------------------------------------------------------- manifest / offline


def test_manifest_artifact_and_table_checksums():
    artifact = MANIFEST["artifact"]
    raw = (ROOT / artifact["path"]).read_bytes()
    assert artifact["sha256"] == hashlib.sha256(raw).hexdigest()
    assert artifact["bytes"] == len(raw)
    expected = {
        "provinces": GAZ["provinces"], "province_aliases": GAZ["province_aliases"],
        "cities": GAZ["cities"], "districts": GAZ["districts"],
        "living_areas": GAZ["living_areas"], "poi": GAZ["poi"],
    }
    assert set(MANIFEST["tables"]) == set(expected)
    for name, table in expected.items():
        entry = MANIFEST["tables"][name]
        assert entry["sha256"] == table_sha(table), name
        assert entry["records"] == len(table), name


def test_manifest_metadata_per_table():
    required = {"source", "license", "redistribution", "coverage", "freshness", "build_command", "sha256", "records", "provenance"}
    for name, entry in MANIFEST["tables"].items():
        assert required <= set(entry), name
        assert isinstance(entry["provenance"], str) and entry["provenance"].strip(), name
        assert isinstance(entry["coverage"].get("covered"), str) and entry["coverage"]["covered"]
        assert isinstance(entry["coverage"].get("not_covered"), list) and entry["coverage"]["not_covered"]
        assert entry["redistribution"] in {
            "allowed", "allowed_with_attribution", "allowed_with_attribution_and_share_alike",
            "not_allowed", "unknown",
        }
        sources = entry["source"] if isinstance(entry["source"], list) else [entry["source"]]
        for source_id in sources:
            assert source_id in MANIFEST["sources"], (name, source_id)


def test_manifest_sources_licenses_and_limitations():
    assert set(MANIFEST["sources"]) == set(GAZ["sources"])
    for source_id, source in MANIFEST["sources"].items():
        assert source["license"], source_id
        assert source["redistribution"] in {
            "allowed", "allowed_with_attribution", "allowed_with_attribution_and_share_alike",
            "not_allowed", "unknown",
        }
    assert len(MANIFEST["limitations"]) >= 5
    assert all(isinstance(item, str) and len(item) > 20 for item in MANIFEST["limitations"])
    assert MANIFEST["sqlite"]["used"] is False
    assert MANIFEST["sqlite"]["measurements"]["json_load_ms_median"] >= 0


def test_runtime_is_offline():
    source = (ROOT / "app" / "geo.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(?:import|from)\s+(?:requests|urllib|http|socket|aiohttp|httpx)\b", source, re.M)
    assert "http://" not in source and "https://" not in source
