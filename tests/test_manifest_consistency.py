"""MANIFEST must describe the files that are actually shipped (Haiku RC-1 finding F3)."""
import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "data" / "geo" / "MANIFEST.json").read_text(encoding="utf-8"))


def test_listed_checksums_match_files_on_disk():
    assert MANIFEST["checksums"], "no checksums recorded"
    for rel, digest in MANIFEST["checksums"].items():
        path = ROOT / rel
        assert path.is_file(), f"MANIFEST lists a file that does not exist: {rel}"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, f"stale checksum: {rel}"


def test_artifact_record_count_is_sum_of_tables():
    assert MANIFEST["artifact"]["records"] == sum(t["records"] for t in MANIFEST["tables"].values())


def test_every_gazetteer_table_has_license_and_source():
    for name, table in MANIFEST["tables"].items():
        assert table.get("license") and table.get("source"), name
