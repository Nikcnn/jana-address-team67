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

_PROVINCE_MARKERS = ("область", "облысы", " обл", "region", "oblast", "oblysy", "province")

def find_province(text: str):
    """Only accept explicit oblast context ('... область' / '... облысы' / '... обл.' /
    Latin '... region|oblast|oblysy|province')."""
    if not isinstance(text, str):
        return None, None
    matches = []
    for entry in get_data().get("province_aliases", []):
        name = entry["name"]
        for alias in list(entry.get("aliases", [name])) + list(entry.get("extra_aliases", [])):
            marker = alias.casefold()
            if not any(word in marker for word in _PROVINCE_MARKERS):
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

# Area markers that may precede a standalone living-area name ('ж/м Пришахтинск').
_AREA_PREFIX = re.compile(r"(?<!\w)(?:ж/м|жм|жилой\s+массив|мкр|мкрн|микрорайон|тұрғын\s+алап)\.?\s*$", re.I)
_AREA_STREET_BEFORE = re.compile(r"(?<!\w)(?:" + _marker_alt(_STREET_MARKERS + ("пр-т", "пр-кт", "шоссе", "проезд", "наб", "набережная", "квартал", "road", "rd", "lane", "ln", "blvd", "boulevard")) + r")\.?\s*$", re.I)
_AREA_STREET_AFTER = re.compile(r"^\s*\.?\s*(?:" + _marker_alt(_STREET_MARKERS + ("пр-т", "пр-кт", "шоссе", "проезд", "наб", "road", "rd", "lane", "ln", "blvd", "boulevard", "мкр", "мкрн", "микрорайон", "шағын", "ықшам")) + r")(?!\w)", re.I)

_STANDALONE_CACHE: dict = {}

def _standalone_areas():
    """Flagged areas plus one combined alias prefilter, rebuilt only when the data object changes."""
    data = get_data()
    cached = _STANDALONE_CACHE.get("entry")
    if cached is None or cached[0] is not data:
        areas = [a for a in data.get("living_areas", []) if a.get("standalone")]
        aliases = sorted({al for a in areas for al in a.get("aliases", [a["name"]])}, key=len, reverse=True)
        body = "|".join(re.escape(al).replace(r"\ ", r"\s+").replace(r"\-", r"[\s\-]") for al in aliases)
        prefilter = re.compile(r"(?<![\w])(?:" + body + r")(?![\w])", re.I) if aliases else None
        cached = (data, areas, prefilter)
        _STANDALONE_CACHE["entry"] = cached
    return cached[1], cached[2]

# Shared with parser.LANDMARK; Latin markers (TRC/TC/mall/shop) as whole words, any case.
LANDMARK_MARKERS = r"магазин|маркет|аптека|qazpost|казпочта|трц|тц|школа|возле|рядом\s+с|напротив|ориентир|trc|tc|mall|shop"
_AREA_LANDMARK_BEFORE = re.compile(r"(?<!\w)(?:" + LANDMARK_MARKERS + r")(?!\w)\.?\s*[«\"']?\s*$", re.I)
# Street context anywhere in the same comma-delimited segment ('Maikuduk region street 5').
_SEGMENT_STREET = re.compile(r"(?<!\w)(?:" + _marker_alt(_STREET_MARKERS + ("пр-т", "пр-кт", "шоссе", "проезд", "наб", "набережная",
    "road", "rd", "lane", "ln", "blvd", "boulevard", "avn")) + r")(?!\w)", re.I)

def _district_matches(area: dict, district: str) -> bool:
    """Explicit district agrees with the area's dictionary district (name or alias)."""
    want = _district_key(district)
    owner = area.get("district")
    keys = {_district_key(owner)}
    for record in get_data().get("districts", []):
        if record.get("name") == owner and record.get("city") == area.get("city"):
            keys |= {_district_key(a) for a in record.get("aliases", [])}
    # Tolerate Russian/Kazakh name-form endings ('Әлихан Бөкейхан' vs '… Бөкейханов'): word-wise prefix.
    def same(a: str, b: str) -> bool:
        wa, wb = a.split(), b.split()
        return len(wa) == len(wb) and all(x.startswith(y) or y.startswith(x) for x, y in zip(wa, wb)) and min(len(a), len(b)) >= 6
    return bool(want) and any(want == k or same(want, k) for k in keys if k)

def find_standalone_area(text: str, city: str | None, province: str | None, district: str | None = None):
    """Known жилой массив mentioned by its own name, without a type marker.

    Whole-token alias match only (so 'Пришахтинская' never matches 'Пришахтинск'),
    never inside street context ('ул. Майкудук', 'Майкудук көшесі'), never directly
    followed by a number (that form is ambiguous with a microdistrict/house number),
    filtered by the explicit city and province. Returns (area, span) for a single
    unique entity, otherwise (None, None). Only entries flagged `standalone` are
    considered, so the bounded scan is over a handful of aliases, not every table.
    """
    if not isinstance(text, str) or not text.strip():
        return None, None
    areas, prefilter = _standalone_areas()
    if prefilter is None or not prefilter.search(text):
        return None, None
    if not city and not province:
        return None, None  # a lone token never invents city/district (needs explicit geo context)
    city_key = _city_key(city) if city else None
    hits = {}
    for area in areas:
        if city_key is not None and _norm(area.get("city")) != city_key:
            continue
        if district and not _district_matches(area, district):
            continue  # explicit different district: never a contradictory pair
        if province and city_key is None:
            owner = next((c for c in get_data().get("cities", []) if c["name"] == area.get("city")), None)
            if owner is None or owner.get("province") != province:
                continue
        for alias in sorted(area.get("aliases", [area["name"]]), key=len, reverse=True):
            for m in boundary_pattern(alias).finditer(text):
                before = text[max(0, m.start() - 24):m.start()]
                after = text[m.end():m.end() + 16]
                if _AREA_STREET_BEFORE.search(before) or _AREA_STREET_AFTER.match(after):
                    continue
                if _AREA_LANDMARK_BEFORE.search(before):
                    continue  # 'магазин Сортировка' / 'ТРЦ Майкудук' is a landmark name
                seg_start = max(text.rfind(sep, 0, m.start()) for sep in ",;\n") + 1
                seg_end = min([i for i in (text.find(sep, m.end()) for sep in ",;\n") if i >= 0] or [len(text)])
                if _SEGMENT_STREET.search(text, seg_start, m.start()) or _SEGMENT_STREET.search(text, m.end(), seg_end):
                    continue  # street context in the same segment wins: ambiguous -> no area
                if re.match(r"^[\s\-]*\d", after):
                    continue
                start = m.start()
                prefix = _AREA_PREFIX.search(before)
                if prefix:
                    start = m.start() - (len(before) - prefix.start())
                hits.setdefault(area["name"], (area, (start, m.end())))
                break
            if area["name"] in hits:
                break
    if len(hits) != 1:
        return None, None
    return next(iter(hits.values()))

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
