"""Offline, conservative, inspectable geo resolver. No calls to any external network.

Data source: data/gazetteer.json (curated public administrative names, per-record
provenance, licenses/checksums in data/geo/MANIFEST.json). Every resolver that may see
the same name more than once (living areas, city districts, POI) returns a value only
when the match is unique; otherwise it returns None (or an explicit candidate list).
"""
from __future__ import annotations
import json
import re
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "gazetteer.json"

@lru_cache(maxsize=1)
def get_data():
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))

@lru_cache(maxsize=8192)
def _norm(value: str | None) -> str:
    """Case/ё/hyphen/space-insensitive comparison key for entity names."""
    if not isinstance(value, str):
        return ""
    text = value.casefold().replace("ё", "е")
    text = re.sub(r"[«»\"'`.,;:!?()\[\]]", " ", text)
    text = re.sub(r"[\s\-_/]+", " ", text)
    return text.strip()

def _city_key(value: str | None) -> str:
    """Normalized city filter key; a known city alias maps to its canonical name."""
    key = _norm(value)
    if not key:
        return ""
    for city in get_data().get("cities", []):
        if key == _norm(city.get("name")) or any(key == _norm(a) for a in city.get("aliases", [])):
            return _norm(city.get("name"))
    return key

def _district_key(value: str | None) -> str:
    """Comparison key for districts; tolerates 'им.', 'район', 'р-н', 'ауданы'."""
    text = _norm(value)
    text = re.sub(r"^(?:им|имени)\s+", "", text)
    text = re.sub(r"\s+(?:район|р н|ауданы|аудан)$", "", text)
    return text.strip()

@lru_cache(maxsize=4096)
def boundary_pattern(alias: str):
    """Whole-token matcher: 'Алматы' never matches inside 'Алматинская'."""
    body = re.escape(alias).replace(r"\ ", r"\s+").replace(r"\-", r"[\s\-]")
    return re.compile(r"(?<![\w])" + body + r"(?![\w])", re.I)

def find_province(text: str):
    """Only accept explicit oblast context ('... область' / '... облысы')."""
    if not isinstance(text, str):
        return None, None
    matches = []
    for entry in get_data().get("province_aliases", []):
        name = entry["name"]
        for alias in entry.get("aliases", [name]):
            marker = alias.casefold()
            if "область" not in marker and "облысы" not in marker:
                continue  # republican cities are provinces too, but appear as cities in text
            m = boundary_pattern(alias).search(text)
            if m:
                matches.append((m.start(), -len(m.group(0)), name, m.span()))
    if not matches:
        for province in get_data().get("provinces", []):
            if "область" not in province.casefold():
                continue
            m = boundary_pattern(province).search(text)
            if m:
                matches.append((m.start(), -len(m.group(0)), province, m.span()))
    return (min(matches)[2], min(matches)[3]) if matches else (None, None)

_DISTRICT_CONTEXT = re.compile(r"(?:\bрайон|\bр-н|\bауданы|\bаудан)\s*$", re.I)
_DISTRICT_AFTER = re.compile(r"^\s+(?:район|ауданы|аудан|district)\b", re.I)
_REGION_CONTEXT = re.compile(r"^\s+(?:облысы|область|облыс|region|oblast|province)\b", re.I)

# A city alias touching a street/area marker is a street or a living area, not a city:
# 'Абай көшесі' is a street, 'Мкр Аксай 3а' is a microdistrict, 'st Abay 5' is a street.
# After the alias a marker only counts when no street name follows it (a number or the
# end of the text comes next); otherwise the alias is a real city and the marker
# introduces the next street: 'Алматы улица Мира 10' keeps Алматы.
_STREET_MARKERS = ("ул", "улица", "проспект", "пр", "пер", "переулок", "бульвар", "б-р",
                   "көше", "көшесі", "даңғыл", "даңғылы", "st", "str", "street", "ave", "avenue")
_AREA_MARKERS = ("мкр", "мкрн", "микрорайон")  # prefix context only: 'Мкр Аксай 3а'

def _marker_alt(markers) -> str:
    """Longest-first alternation, so 'көшесі' wins over 'көше' before the (?!\\w) guard."""
    return "|".join(re.escape(m) for m in sorted(markers, key=len, reverse=True))

_STREET_BEFORE = re.compile(r"(?<!\w)(?:" + _marker_alt(_STREET_MARKERS + _AREA_MARKERS) + r")\.?\s*$", re.I)
_STREET_AFTER = re.compile(r"^\s*\.?\s*(?:" + _marker_alt(_STREET_MARKERS) + r")(?!\w)\.?\s*,?\s*(?:\d|$)", re.I)

def find_city(text: str):
    """Only prominent/marked city mentions; don't infer cities from street or district names."""
    if not isinstance(text, str):
        return None, None
    found = []
    for city in get_data().get("cities", []):
        for alias in city.get("aliases", [city["name"]]):
            for m in boundary_pattern(alias).finditer(text):
                before = text[max(0, m.start() - 18):m.start()]
                after = text[m.end():m.end() + 13]
                if _DISTRICT_CONTEXT.search(before) or _DISTRICT_AFTER.match(after):
                    continue  # e.g. Aktobe's 'район Астана' is a district, not the capital
                if _REGION_CONTEXT.match(after):
                    continue  # e.g. 'Алматы облысы' is Almaty Region, not Almaty city
                if _STREET_BEFORE.search(before) or _STREET_AFTER.match(after):
                    continue  # e.g. 'Абай көшесі' / 'Abay st' / 'st Abay' is a street, not Abay city
                marked = bool(re.search(r"(?:\bг(?:ород|ор|\.)?\s*|\bпос(?:ёлок|елок|\.)?\s*|\bқаласы\s*)$", before, re.I)) or bool(re.match(r"\s+қаласы\b", after, re.I))
                leading = not text[:m.start()].strip(" \t\n,;/.-")
                if marked or leading or (m.start() < 42 and not re.search(r"(?:ул\.?|улица|проспект|мкр|район|пр\.)\s*$", before, re.I)):
                    found.append((0 if marked else 1, m.start(), -len(alias), city, m.span()))
    if not found:
        return None, None
    best = min(found, key=lambda x: x[:3])
    return best[3], best[4]

def resolve_area(name: str, city: str | None):
    """Resolve a microdistrict/ЖК name; unique match only, optionally scoped to city."""
    key = _norm(name)
    if not key:
        return None
    city_key = _city_key(city) if city else None
    matches = []
    for area in get_data().get("living_areas", []):
        if city_key is not None and _norm(area.get("city")) != city_key:
            continue
        if any(_norm(alias) == key for alias in area.get("aliases", [area["name"]])):
            matches.append(area)
    return matches[0] if len(matches) == 1 else None

def city_for_district(district: str):
    """Return the city owning the district; unique match only across all cities."""
    key = _district_key(district)
    if not key:
        return None
    cities = {c["name"]: c for c in get_data().get("cities", [])}
    owners = {}
    for record in get_data().get("districts", []):
        names = {_district_key(record["name"])} | {_district_key(a) for a in record.get("aliases", [])}
        if key in names and record.get("city") in cities:
            owners[record["city"]] = cities[record["city"]]
    if not owners:
        for city in get_data().get("cities", []):
            if any(_district_key(d) == key for d in city.get("districts", [])):
                owners[city["name"]] = city
    return next(iter(owners.values())) if len(owners) == 1 else None

def lookup_poi(name: str, city: str | None = None):
    """Return POI candidates by exact entity name or alias (never by raw address).

    Contract: matching is whole-name, case/ё/hyphen-insensitive, against
    `name`/`aliases` only. `city` is an optional exact-scope filter (same
    normalization). Returns a deterministically ordered list of records:
      1 candidate -> unique branch; 0 or >1 -> no unique answer.
    Use lookup_poi_unique() for the single-record form.
    """
    key = _norm(name)
    if not key:
        return []
    city_key = _city_key(city) if city else None
    matches = [
        poi for poi in get_data().get("poi", [])
        if (city_key is None or _norm(poi.get("city")) == city_key)
        and any(_norm(alias) == key for alias in poi.get("aliases", [poi["name"]]))
    ]
    return sorted(matches, key=lambda p: (str(p.get("city") or ""), str(p.get("branch_id") or "")))

def lookup_poi_unique(name: str, city: str | None = None):
    """Single POI branch or None when the match is missing or ambiguous."""
    matches = lookup_poi(name, city)
    return matches[0] if len(matches) == 1 else None
