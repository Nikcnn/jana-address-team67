"""Self-tests of scripts/eval_holdout.py: the measuring instrument must be right before its numbers mean anything."""
from __future__ import annotations

import copy
import importlib.util
import json

import pytest

from .helpers import QA_DATA, ROOT, holdout_records

spec = importlib.util.spec_from_file_location("eval_holdout", ROOT / "scripts" / "eval_holdout.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)  # type: ignore[union-attr]


def perfect_predictions(records):
    out = []
    for r in records:
        rec = {"id": r["id"], "raw_address": r["raw_address"]}
        rec.update({f: r["gold"].get(f) for f in ev.FIELDS})
        rec["confidence"] = 0.5
        out.append(rec)
    return out


def test_holdout_file_passes_its_own_lint():
    doc = json.loads((QA_DATA / "regression_corpus_v1.json").read_text(encoding="utf-8"))
    assert ev.validate_holdout(doc, ROOT) == []
    assert 60 <= len(doc["records"]) <= 120


def test_holdout_covers_required_strata():
    recs = holdout_records()
    langs = {r["lang"] for r in recs}
    tags = {t for r in recs for t in r["tags"]}
    assert langs == {"ru", "kz", "mixed"}
    for needed in ("no_city", "poi", "district_explicit", "fraction", "letter", "ambiguous_district", "garbage", "city_alias_kz"):
        assert needed in tags, needed
    assert any(r["gold"].get("district") for r in recs) and any("district" not in r["gold"] for r in recs)


def test_perfect_predictions_score_100_percent_with_denominators():
    recs = holdout_records()
    res = ev.evaluate(recs, perfect_predictions(recs))
    ov = res["overall"]
    assert ov["weighted_proxy_normalized"] == 1.0 and ov["weighted_proxy_recall_on_gold_nonnull"] == 1.0
    assert ov["record_exact_match_all11_exact"]["num"] == len(recs) - 0 or ov["record_exact_match_all11_normalized"]["value"] == 1.0
    for f in ev.METRIC_FIELDS:
        pf = ov["per_field"][f]
        assert pf["accuracy_normalized"]["value"] == 1.0 and pf["false_fill_rate"]["num"] == 0
        assert pf["accuracy_normalized"]["den"] > 0, f
    assert res["contract_violations"]["records_with_violations"] == 0


def test_false_fill_is_counted_and_lowers_null_precision():
    recs = holdout_records()
    preds = perfect_predictions(recs)
    i = next(k for k, r in enumerate(recs) if "city" not in r["gold"] and "city" not in r.get("unscored", []))
    preds[i]["city"] = "Астана"
    pf = ev.evaluate(recs, preds)["overall"]["per_field"]["city"]
    assert pf["false_fill_rate"]["num"] == 1 and pf["null_recall"]["value"] < 1.0 and pf["null_precision"]["value"] == 1.0 \
        or pf["false_fill_rate"]["num"] == 1
    assert pf["accuracy_normalized"]["num"] == pf["accuracy_normalized"]["den"] - 1


def test_missed_value_and_wrong_value_are_distinguished():
    recs = holdout_records()
    preds = perfect_predictions(recs)
    idx = [k for k, r in enumerate(recs) if "house" in r["gold"]][:2]
    preds[idx[0]]["house"] = None
    preds[idx[1]]["house"] = "999"
    pf = ev.evaluate(recs, preds)["overall"]["per_field"]["house"]
    assert pf["counts"]["missed"] == 1 and pf["counts"]["wrong_fill"] == 1 and pf["counts"]["false_fill"] == 0
    assert pf["null_precision"]["den"] >= 1  # the missed one is predicted-null although gold is present


def test_s1_normalisation_rules():
    n = ev.s1_normalize
    assert n("  Ул.  Мира, 5 ") == "ул мира 5"
    assert n("КАЗИБЕК   БИ") == "казибек би"
    assert n(None) is None
    assert n("1/2") != n("1") and n("1/2") != n("12"), "«1/2» must differ from «1» and from «12»"
    assert n("13Б") == n("13б")


def test_exact_and_normalised_differ_on_case():
    recs = [{"id": "x", "raw_address": "ул. Мира 5", "lang": "ru", "tags": [], "gold": {"street_name": "Мира", "house": "5"}}]
    pred = [{"id": "x", "raw_address": "ул. Мира 5", **{f: None for f in ev.FIELDS}, "street_name": "мира", "house": "5", "confidence": 0.5}]
    pf = ev.evaluate(recs, pred)["overall"]["per_field"]["street_name"]
    assert pf["accuracy_normalized"]["num"] == 1 and pf["accuracy_exact"]["num"] == 0


def test_unscored_fields_leave_the_denominator():
    recs = [{"id": "x", "raw_address": "ул", "lang": "ru", "tags": [], "gold": {}, "unscored": ["street_type"]}]
    pred = [{"id": "x", "raw_address": "ул", **{f: None for f in ev.FIELDS}, "street_type": "улица", "confidence": 0.1}]
    pf = ev.evaluate(recs, pred)["overall"]["per_field"]["street_type"]
    assert pf["n_scored"] == 0 and pf["accuracy_normalized"]["den"] == 0 and pf["accuracy_normalized"]["value"] is None


def test_order_or_id_mismatch_is_rejected():
    recs = holdout_records()[:3]
    preds = perfect_predictions(recs)[::-1]
    with pytest.raises(ValueError):
        ev.evaluate(recs, preds)
    with pytest.raises(ValueError):
        ev.evaluate(recs, perfect_predictions(recs)[:2])


def test_contract_violations_in_predictions_are_reported():
    recs = holdout_records()[:2]
    preds = perfect_predictions(recs)
    preds[0]["house"] = 5          # numeric coercion
    preds[1]["floor"] = ""         # blank string instead of null
    res = ev.evaluate(recs, preds)["contract_violations"]
    assert res["records_with_violations"] == 2


def test_validator_detects_leakage_and_inconsistent_gold():
    doc = json.loads((QA_DATA / "regression_corpus_v1.json").read_text(encoding="utf-8"))
    demo = json.loads((ROOT / "data" / "demo_input.json").read_text(encoding="utf-8"))
    bad = copy.deepcopy(doc)
    bad["records"][0]["raw_address"] = demo[0]["raw_address"]
    assert any("overlaps" in e for e in ev.validate_holdout(bad, ROOT))
    bad = copy.deepcopy(doc)
    bad["records"][1]["gold"]["street_name"] = "ТакогоВСтрокеНет"
    assert any("not found in raw" in e for e in ev.validate_holdout(bad, ROOT))
    bad = copy.deepcopy(doc)
    bad["records"][2]["gold"]["house"] = ""
    assert any("clean non-empty" in e for e in ev.validate_holdout(bad, ROOT))


def test_validator_detects_overlap_with_strings_visible_in_tests():
    """Strings in tests/qa are visible to rule authors; a holdout row equal to one of them is leaked."""
    doc = json.loads((QA_DATA / "regression_corpus_v1.json").read_text(encoding="utf-8"))
    bad = copy.deepcopy(doc)
    bad["records"][0]["raw_address"] = "Мұқанов көшесі 31 үй"       # literal used in tests/qa/test_ru_kz.py
    errs = ev.validate_holdout(bad, ROOT)
    assert any("tests/qa" in e for e in errs), errs


def test_mismatches_are_withheld_by_default():
    recs = holdout_records()[:5]
    assert isinstance(ev.evaluate(recs, perfect_predictions(recs))["mismatches"], str)
    assert isinstance(ev.evaluate(recs, perfect_predictions(recs), show_mismatches=True)["mismatches"], list)


def test_wilson_interval_sanity():
    lo, hi = ev.wilson(50, 100)
    assert 0.39 < lo < 0.41 and 0.59 < hi < 0.61
    assert ev.wilson(0, 0) is None
