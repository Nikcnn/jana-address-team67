"""Merge verified, licensed geographic records into the local gazetteer.

This script never invents provenance: every imported row must resolve to a source id
registered in `gazetteer.json -> sources` (via the CSV `source` column or `--source`).
Unknown provinces/cities are rejected instead of silently extending the schema.

Usage (run from the project root):
  python scripts/import_gazetteer_csv.py cities.csv
  python scripts/import_gazetteer_csv.py --table cities --source wiki-cities-ru cities.csv
  python scripts/import_gazetteer_csv.py --table districts --source wiki-districts districts.csv
  python scripts/import_gazetteer_csv.py --table poi --source synthetic poi.csv
  python scripts/import_gazetteer_csv.py --table provinces --source wiki-cities-ru provinces.csv
  python scripts/import_gazetteer_csv.py --refresh-manifest

CSV headers (UTF-8, optional BOM; `aliases` separated by `;`):
  cities:    name,province,aliases[,source,provenance]
  provinces: name,aliases[,source,provenance]
  districts: name,city,aliases[,source,provenance]
  poi:       name,type,city,district,branch_id,aliases[,linked_address,synthetic,source,provenance]

DO NOT run with customer addresses, only geographic names. Back up the file and verify
licensing before importing; then run the test suite (it re-checks MANIFEST checksums).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "gazetteer.json"
MANIFEST_PATH = ROOT / "data" / "geo" / "MANIFEST.json"
TABLES = ("provinces", "province_aliases", "cities", "districts", "living_areas", "poi")
MANIFESTED = ("provinces", "province_aliases", "cities", "districts", "living_areas", "poi")


def table_sha(table) -> str:
    raw = json.dumps(table, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def split_aliases(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(";") if part.strip()]


def add_aliases(record: dict, name: str, raw_aliases: str | None) -> None:
    merged = [name] + record.get("aliases", []) + split_aliases(raw_aliases)
    seen, aliases = set(), []
    for alias in merged:
        if alias and alias.casefold() not in seen:
            seen.add(alias.casefold())
            aliases.append(alias)
    record["aliases"] = aliases


def provenance_for(row: dict, source_id: str, explicit: str | None) -> str:
    if explicit:
        return explicit.strip()
    if row.get("provenance", "").strip():
        return row["provenance"].strip()
    return f"imported via scripts/import_gazetteer_csv.py on {date.today().isoformat()} from source '{source_id}'; not independently re-verified"


def require_source(row: dict, args, data: dict) -> str:
    source_id = (row.get("source") or args.source or "").strip()
    if not source_id:
        raise SystemExit("ERROR: every row needs a source id (CSV column 'source' or --source)")
    if source_id not in data["sources"]:
        known = ", ".join(sorted(data["sources"]))
        raise SystemExit(f"ERROR: unknown source id '{source_id}'. Register it in gazetteer.json sources first. Known: {known}")
    return source_id


def merge_city(data: dict, row: dict, args) -> None:
    name = row["name"].strip()
    province = row["province"].strip()
    if province not in data["provinces"]:
        raise SystemExit(f"ERROR: unknown province '{province}' for city '{name}' (schema growth must be explicit)")
    source_id = require_source(row, args, data)
    existing = next((c for c in data["cities"] if c["name"] == name), None)
    if existing:
        if existing["province"] != province:
            raise SystemExit(f"ERROR: province conflict for '{name}': {existing['province']} vs {province}")
        add_aliases(existing, name, row.get("aliases"))
        existing.setdefault("provenance", provenance_for(row, source_id, args.provenance))
        existing.setdefault("source", source_id)
    else:
        record = {"name": name, "province": province, "aliases": [name]}
        add_aliases(record, name, row.get("aliases"))
        record["provenance"] = provenance_for(row, source_id, args.provenance)
        record["source"] = source_id
        data["cities"].append(record)


def merge_province(data: dict, row: dict, args) -> None:
    name = row["name"].strip()
    if name not in data["provinces"]:
        raise SystemExit(f"ERROR: province '{name}' is not part of the frozen 17+3 list")
    source_id = require_source(row, args, data)
    entry = next((e for e in data["province_aliases"] if e["name"] == name), None)
    if entry is None:
        entry = {"name": name, "aliases": [name]}
        data["province_aliases"].append(entry)
    add_aliases(entry, name, row.get("aliases"))
    entry.setdefault("provenance", provenance_for(row, source_id, args.provenance))
    entry.setdefault("source", source_id)


def merge_district(data: dict, row: dict, args) -> None:
    name = row["name"].strip()
    city_name = row["city"].strip()
    city = next((c for c in data["cities"] if c["name"] == city_name), None)
    if city is None:
        raise SystemExit(f"ERROR: unknown city '{city_name}' for district '{name}'")
    source_id = require_source(row, args, data)
    existing = next((d for d in data["districts"] if d["name"] == name and d["city"] == city_name), None)
    if existing:
        add_aliases(existing, name, row.get("aliases"))
        existing.setdefault("provenance", provenance_for(row, source_id, args.provenance))
        existing.setdefault("source", source_id)
    else:
        record = {"name": name, "city": city_name, "aliases": [name]}
        add_aliases(record, name, row.get("aliases"))
        record["provenance"] = provenance_for(row, source_id, args.provenance)
        record["source"] = source_id
        data["districts"].append(record)
    if name not in city.setdefault("districts", []):
        city["districts"].append(name)


def merge_poi(data: dict, row: dict, args) -> None:
    name = row["name"].strip()
    branch_id = (row.get("branch_id") or "").strip()
    if not branch_id:
        raise SystemExit(f"ERROR: POI '{name}' needs a branch_id")
    if any(p.get("branch_id") == branch_id for p in data["poi"]):
        raise SystemExit(f"ERROR: duplicate branch_id '{branch_id}'")
    source_id = require_source(row, args, data)
    record = {
        "name": name,
        "aliases": [name],
        "type": (row.get("type") or "").strip() or "unknown",
        "city": (row.get("city") or "").strip() or None,
        "district": (row.get("district") or "").strip() or None,
        "branch_id": branch_id,
        "linked_address": (row.get("linked_address") or "").strip() or None,
        "synthetic": str(row.get("synthetic", "")).strip().lower() in {"1", "true", "yes"},
        "provenance": provenance_for(row, source_id, args.provenance),
        "source": source_id,
    }
    add_aliases(record, name, row.get("aliases"))
    data["poi"].append(record)


MERGERS = {"cities": merge_city, "provinces": merge_province, "districts": merge_district, "poi": merge_poi}
REQUIRED = {
    "cities": ("name", "province"),
    "provinces": ("name",),
    "districts": ("name", "city"),
    "poi": ("name", "branch_id"),
}


def refresh_manifest(data: dict) -> bool:
    if not MANIFEST_PATH.exists():
        return False
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    raw = DATA_PATH.read_bytes()
    manifest["artifact"]["sha256"] = hashlib.sha256(raw).hexdigest()
    manifest["artifact"]["bytes"] = len(raw)
    for name in MANIFESTED:
        if name in manifest.get("tables", {}):
            manifest["tables"][name]["sha256"] = table_sha(data[name])
            manifest["tables"][name]["records"] = len(data[name])
    # code/data files listed under "checksums" are recomputed from disk; entries
    # for files that do not exist in this tree (e.g. tests, not shipped) are dropped.
    for rel in list(manifest.get("checksums", {})):
        path = ROOT / rel
        if path.is_file():
            manifest["checksums"][rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            del manifest["checksums"][rel]
    manifest["artifact"]["records"] = sum(t.get("records", 0) for t in manifest.get("tables", {}).values())
    manifest["artifact"]["records_definition"] = "sum of tables.*.records (provinces + province_aliases + cities + districts + living_areas + poi)"
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge verified geographic records into data/gazetteer.json")
    parser.add_argument("csv_path", nargs="?", help="CSV file to import")
    parser.add_argument("--table", choices=sorted(MERGERS), default="cities")
    parser.add_argument("--source", default=None, help="source id registered in gazetteer.json -> sources")
    parser.add_argument("--provenance", default=None, help="explicit provenance string for all imported rows")
    parser.add_argument("--dry-run", action="store_true", help="validate and print counts, write nothing")
    parser.add_argument("--refresh-manifest", action="store_true", help="recompute MANIFEST checksums only")
    args = parser.parse_args(argv)

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if args.refresh_manifest:
        if args.csv_path:
            parser.error("--refresh-manifest does not take a CSV path")
        updated = refresh_manifest(data)
        print("Manifest refreshed" if updated else "No manifest found; nothing to refresh")
        return 0
    if not args.csv_path:
        parser.error("csv_path is required unless --refresh-manifest is used")

    before = {name: len(data[name]) for name in TABLES}
    with open(args.csv_path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [field for field in REQUIRED[args.table] if field not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"ERROR: CSV is missing required columns for {args.table}: {', '.join(missing)}")
        for line_no, row in enumerate(reader, start=2):
            try:
                MERGERS[args.table](data, row, args)
            except SystemExit as exc:
                raise SystemExit(f"{exc} (CSV line {line_no})") from None
            except KeyError as exc:
                raise SystemExit(f"ERROR: missing field {exc} (CSV line {line_no})") from None

    after = {name: len(data[name]) for name in TABLES}
    added = {name: after[name] - before[name] for name in TABLES if after[name] != before[name]}
    print(f"{args.table}: rows now {after[args.table]} (delta {after[args.table] - before[args.table]}); changed tables: {added or 'aliases only'}")
    if args.dry_run:
        print("Dry run: gazetteer not written")
        return 0
    DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    refreshed = refresh_manifest(data)
    print("Wrote", DATA_PATH.relative_to(ROOT), "| manifest refreshed" if refreshed else "| no manifest to refresh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
