#!/usr/bin/env python
"""Proxy evaluation of an address parser on the LOCAL synthetic holdout (data/qa/regression_corpus_v1.json).

!!! PROXY METRICS ONLY. NOT the official hidden score. The holdout is synthetic, single-annotator
and small; confidence intervals are wide. Never quote these numbers as the system's accuracy.

Metric rules
  * S1-normalised match (organiser rule, as read from the contract C9): lower-case, punctuation -> space,
    collapse whitespace. Punctuation becomes a SPACE (not deleted) so "1/2" != "12" and "1/2" != "1".
    The organisers' exact implementation is TBD, therefore results are labelled proxy.
  * `exact` = byte-identical string, reported next to the normalised numbers.
  * Hidden-metric groups (C9): core(province,city,street_name,house)=60%, details(apartment,entrance,
    floor)=25%, reference(district,living_area)=15%. street_type / landmark / confidence are diagnostic only.
    Weighted proxy = 0.60*mean(core) + 0.25*mean(details) + 0.15*mean(reference)  (intra-group weights TBD).
  * Fields listed in a record's `unscored` are dropped from every denominator for that record.

Usage (cwd = repo copy):
  python scripts/eval_holdout.py                      # direct parse_batch, text summary on stderr, JSON on stdout
  python scripts/eval_holdout.py --json-out r.json    # also write JSON file
  python scripts/eval_holdout.py --url http://127.0.0.1:8000/api/v1/addresses/parse-batch   # via REST
  python scripts/eval_holdout.py --predictions out.json   # score saved 14-field output (aligned by id)
  python scripts/eval_holdout.py --validate           # lint the holdout file only (exit 2 on errors)
  python scripts/eval_holdout.py --show-mismatches    # QA-internal: per-row diffs (withheld by default, anti-leakage)
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import re
import sys
import unicodedata
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
HOLDOUT_PATH = ROOT / "data" / "qa" / "regression_corpus_v1.json"
FIELDS = ["province", "city", "district", "living_area", "street_type", "street_name",
          "house", "apartment", "entrance", "floor", "landmark"]
OUTPUT_KEYS = ["id", "raw_address", *FIELDS, "confidence"]
GROUPS = {"core": (["province", "city", "street_name", "house"], 0.60),
          "details": (["apartment", "entrance", "floor"], 0.25),
          "reference": (["district", "living_area"], 0.15)}
METRIC_FIELDS = [f for fields, _ in GROUPS.values() for f in fields]
DIAGNOSTIC_FIELDS = ["street_type", "landmark"]
DISCLAIMER = ("PROXY METRICS on a local synthetic holdout. NOT the official hidden score. "
              "Hidden-set quality: NOT MEASURED.")
S1_RULE = "lower(); characters of unicode category P*/S* -> space; collapse whitespace; strip"
LANGS = {"ru", "kz", "mixed"}
HOUSE_LIKE = {"house", "apartment", "entrance", "floor"}
MIN_LEAK_LEN = 8  # normalised length from which a string shared with tests/qa counts as leakage


# ----------------------------------------------------------------------------- normalisation / stats
def s1_normalize(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):  # a contract violation by the predictor must be scored/reported, not crash the harness
        value = str(value)
    lowered = value.lower()
    spaced = "".join(" " if unicodedata.category(ch)[0] in "PS" else ch for ch in lowered)
    return " ".join(spaced.split())


def ratio(num: int, den: int) -> dict[str, Any]:
    return {"num": num, "den": den, "value": (round(num / den, 4) if den else None)}


def wilson(num: int, den: int, z: float = 1.96) -> list[float] | None:
    if not den:
        return None
    p = num / den
    denom = 1 + z * z / den
    centre = (p + z * z / (2 * den)) / denom
    half = z * math.sqrt(p * (1 - p) / den + z * z / (4 * den * den)) / denom
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def is_str_or_none(v: Any) -> bool:
    return v is None or isinstance(v, str)


def contract_violations(pred: dict[str, Any]) -> list[str]:
    """Cheap shape check of one prediction (full contract suite lives in tests/qa)."""
    bad: list[str] = []
    if list(pred.keys()) != OUTPUT_KEYS:
        bad.append("keys_or_order")
    for f in FIELDS:
        v = pred.get(f)
        if not is_str_or_none(v):
            bad.append(f"{f}:type")
        elif isinstance(v, str) and not v.strip():
            bad.append(f"{f}:blank_string")
    return bad


# ----------------------------------------------------------------------------- field-level counting
class FieldCounter:
    """Accumulates the confusion counts for one field over a set of scored records."""

    def __init__(self) -> None:
        self.n = self.gold_null = self.gold_nonnull = 0
        self.correct_exact = self.correct_norm = 0
        self.tp_null = self.pred_null = 0
        self.false_fill = self.wrong_fill = self.missed = 0
        self.nonnull_correct_norm = 0

    def add(self, gold: str | None, pred: str | None) -> None:
        self.n += 1
        exact_ok = gold == pred
        norm_ok = s1_normalize(gold) == s1_normalize(pred)
        self.correct_exact += exact_ok
        self.correct_norm += norm_ok
        if pred is None:
            self.pred_null += 1
        if gold is None:
            self.gold_null += 1
            if pred is None:
                self.tp_null += 1
            else:
                self.false_fill += 1
        else:
            self.gold_nonnull += 1
            if norm_ok:
                self.nonnull_correct_norm += 1
            elif pred is None:
                self.missed += 1
            else:
                self.wrong_fill += 1

    def report(self) -> dict[str, Any]:
        acc = ratio(self.correct_norm, self.n)
        acc["wilson95"] = wilson(self.correct_norm, self.n)
        return {
            "n_scored": self.n,
            "accuracy_normalized": acc,
            "accuracy_exact": ratio(self.correct_exact, self.n),
            "recall_on_gold_nonnull": ratio(self.nonnull_correct_norm, self.gold_nonnull),
            "null_precision": ratio(self.tp_null, self.pred_null),
            "null_recall": ratio(self.tp_null, self.gold_null),
            "false_fill_rate": ratio(self.false_fill, self.gold_null),
            "wrong_fill_rate": ratio(self.wrong_fill, self.gold_nonnull),
            "missed_rate": ratio(self.missed, self.gold_nonnull),
            "counts": {"gold_null": self.gold_null, "gold_nonnull": self.gold_nonnull,
                       "false_fill": self.false_fill, "wrong_fill": self.wrong_fill, "missed": self.missed},
        }


def scored_fields(rec: dict[str, Any]) -> list[str]:
    skip = set(rec.get("unscored", []))
    return [f for f in FIELDS if f not in skip]


def gold_value(rec: dict[str, Any], field: str) -> str | None:
    return rec.get("gold", {}).get(field)


def slice_report(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    """Full metric block for a subset of (gold_record, prediction) pairs."""
    counters = {f: FieldCounter() for f in FIELDS}
    exact_metric = norm_metric = norm_all = exact_all = 0
    for rec, pred in pairs:
        ok_norm_metric = ok_exact_metric = ok_norm_all = ok_exact_all = True
        for f in scored_fields(rec):
            g, p = gold_value(rec, f), pred.get(f)
            counters[f].add(g, p if isinstance(p, str) or p is None else str(p))
            n_ok = s1_normalize(g) == s1_normalize(p if isinstance(p, str) or p is None else str(p))
            e_ok = g == p
            ok_norm_all &= n_ok
            ok_exact_all &= e_ok
            if f in METRIC_FIELDS:
                ok_norm_metric &= n_ok
                ok_exact_metric &= e_ok
        norm_metric += ok_norm_metric
        exact_metric += ok_exact_metric
        norm_all += ok_norm_all
        exact_all += ok_exact_all
    n = len(pairs)
    per_field = {f: counters[f].report() for f in FIELDS}
    groups: dict[str, Any] = {}
    weighted, weight_used = 0.0, 0.0
    weighted_nn, weight_used_nn = 0.0, 0.0
    for name, (fields, weight) in GROUPS.items():
        vals = [per_field[f]["accuracy_normalized"]["value"] for f in fields
                if per_field[f]["accuracy_normalized"]["value"] is not None]
        vals_exact = [per_field[f]["accuracy_exact"]["value"] for f in fields
                      if per_field[f]["accuracy_exact"]["value"] is not None]
        vals_nn = [per_field[f]["recall_on_gold_nonnull"]["value"] for f in fields
                   if per_field[f]["recall_on_gold_nonnull"]["value"] is not None]
        mean = round(sum(vals) / len(vals), 4) if vals else None
        mean_nn = round(sum(vals_nn) / len(vals_nn), 4) if vals_nn else None
        groups[name] = {"fields": fields, "weight": weight, "mean_accuracy_normalized": mean,
                        "mean_accuracy_exact": round(sum(vals_exact) / len(vals_exact), 4) if vals_exact else None,
                        "mean_recall_on_gold_nonnull": mean_nn}
        if mean is not None:
            weighted += weight * mean
            weight_used += weight
        if mean_nn is not None:
            weighted_nn += weight * mean_nn
            weight_used_nn += weight
    return {
        "n_records": n,
        "per_field": per_field,
        "groups": groups,
        "weighted_proxy_normalized": round(weighted / weight_used, 4) if weight_used else None,
        "weighted_proxy_weight_covered": round(weight_used, 2),
        "weighted_proxy_recall_on_gold_nonnull": round(weighted_nn / weight_used_nn, 4) if weight_used_nn else None,
        "weighted_proxy_note": ("accuracy counts null==null as correct and is inflated by sparse fields; "
                                "recall_on_gold_nonnull counts only records whose gold value is present"),
        "record_exact_match_metric_fields_normalized": ratio(norm_metric, n),
        "record_exact_match_metric_fields_exact": ratio(exact_metric, n),
        "record_exact_match_all11_normalized": ratio(norm_all, n),
        "record_exact_match_all11_exact": ratio(exact_all, n),
    }


def compact(block: dict[str, Any]) -> dict[str, Any]:
    """Short view of a slice for breakdown tables."""
    return {
        "n_records": block["n_records"],
        "weighted_proxy_normalized": block["weighted_proxy_normalized"],
        "weighted_proxy_recall_on_gold_nonnull": block["weighted_proxy_recall_on_gold_nonnull"],
        "core": block["groups"]["core"]["mean_accuracy_normalized"],
        "details": block["groups"]["details"]["mean_accuracy_normalized"],
        "reference": block["groups"]["reference"]["mean_accuracy_normalized"],
        "record_exact_metric_normalized": block["record_exact_match_metric_fields_normalized"],
    }


# ----------------------------------------------------------------------------- breakdowns
def basis_of(rec: dict[str, Any], field: str) -> str:
    return rec.get("basis", {}).get(field, "explicit")


def basis_breakdown(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    """Accuracy on gold-NON-NULL values split by how the gold value relates to the raw text."""
    cells: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for rec, pred in pairs:
        for f in scored_fields(rec):
            g = gold_value(rec, f)
            if g is None:
                continue
            cell = cells[basis_of(rec, f)][f]
            cell[1] += 1
            cell[0] += s1_normalize(g) == s1_normalize(pred.get(f))
    out: dict[str, Any] = {}
    for basis, per_field in cells.items():
        metric_num = sum(v[0] for f, v in per_field.items() if f in METRIC_FIELDS)
        metric_den = sum(v[1] for f, v in per_field.items() if f in METRIC_FIELDS)
        out[basis] = {"metric_fields_pooled": ratio(metric_num, metric_den),
                      "per_field": {f: ratio(v[0], v[1]) for f, v in sorted(per_field.items())}}
    return out


def completeness_bucket(rec: dict[str, Any]) -> str:
    k = sum(1 for f in METRIC_FIELDS if gold_value(rec, f) is not None)
    return "0-2 gold fields" if k <= 2 else "3-5 gold fields" if k <= 5 else "6-9 gold fields"


def breakdowns(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    def by(keyfn) -> dict[str, Any]:
        buckets: dict[str, list] = defaultdict(list)
        for rec, pred in pairs:
            for k in keyfn(rec):
                buckets[k].append((rec, pred))
        return {k: compact(slice_report(v)) for k, v in sorted(buckets.items())}

    tag_counts: dict[str, int] = defaultdict(int)
    for rec, _ in pairs:
        for t in rec.get("tags", []):
            tag_counts[t] += 1
    big_tags = {t for t, c in tag_counts.items() if c >= 4}
    return {
        "by_lang": by(lambda r: [r["lang"]]),
        "by_gold_city_missing": by(lambda r: ["city_gold_null" if gold_value(r, "city") is None else "city_gold_present"]),
        "by_completeness": by(lambda r: [completeness_bucket(r)]),
        "by_tag(n>=4)": by(lambda r: [t for t in r.get("tags", []) if t in big_tags]),
        "by_basis_explicit_vs_alias_vs_inferred": basis_breakdown(pairs),
        "missingness_per_field": {
            f: {"gold_null": sum(1 for r, _ in pairs if f in scored_fields(r) and gold_value(r, f) is None),
                "gold_nonnull": sum(1 for r, _ in pairs if f in scored_fields(r) and gold_value(r, f) is not None)}
            for f in FIELDS},
    }


def mismatches(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> list[dict[str, Any]]:
    """Every scored field where the normalised prediction differs from gold (synthetic raws only)."""
    out = []
    for rec, pred in pairs:
        for f in scored_fields(rec):
            g, p = gold_value(rec, f), pred.get(f)
            if s1_normalize(g) != s1_normalize(p if isinstance(p, str) or p is None else str(p)):
                kind = "false_fill" if g is None else "missed" if p is None else "wrong_fill"
                out.append({"id": rec["id"], "raw_address": rec["raw_address"], "field": f, "kind": kind,
                            "gold": g, "pred": p, "metric_field": f in METRIC_FIELDS})
    return out


def evaluate(records: list[dict[str, Any]], preds: list[dict[str, Any]], show_mismatches: bool = False) -> dict[str, Any]:
    if len(records) != len(preds):
        raise ValueError(f"cardinality mismatch: {len(records)} gold vs {len(preds)} predictions")
    pairs = list(zip(records, preds))
    for rec, pred in pairs:
        if pred.get("id") != rec["id"]:
            raise ValueError("prediction order/id mismatch (association is by position)")
    overall = slice_report(pairs)
    bad = {}
    for rec, pred in pairs:
        v = contract_violations(pred)
        if v:
            bad[rec["id"]] = v
    return {"overall": overall, "breakdowns": breakdowns(pairs),
            "contract_violations": {"records_with_violations": len(bad), "examples": dict(list(bad.items())[:10])},
            "mismatches": mismatches(pairs) if show_mismatches else "withheld (anti-leakage policy); use --show-mismatches for QA-internal debugging"}


# ----------------------------------------------------------------------------- holdout loading / lint
def load_holdout(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def other_corpora_raws(root: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for rel in ("data/sample_input.json", "data/demo_input.json", "data/synthetic_corpus_v1.json"):
        p = root / rel
        if p.exists():
            out[rel] = {s1_normalize(r.get("raw_address")) or "" for r in json.loads(p.read_text(encoding="utf-8"))}
    tests_dir = root / "tests" / "qa"
    if tests_dir.is_dir():
        literals: set[str] = set()
        for py in sorted(tests_dir.glob("*.py")):
            for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    norm = s1_normalize(node.value) or ""
                    if len(norm) >= MIN_LEAK_LEN:  # single words / markers cannot leak a pattern
                        literals.add(norm)
        out["tests/qa/*.py (visible to rule authors)"] = literals
    sc = root / "data" / "qa" / "acceptance_scenarios.json"
    if sc.exists():
        out["data/qa/acceptance_scenarios.json"] = {
            s1_normalize(s["raw"]) or "" for s in json.loads(sc.read_text(encoding="utf-8"))["scenarios"]}
    return out


def explicit_in_raw(field: str, gold: str, raw: str) -> bool:
    if field in ("street_name", "landmark", "house", "apartment", "entrance", "floor"):
        pattern = re.escape(gold).replace("/", r"\s*/\s*") if field in HOUSE_LIKE else re.escape(gold)
        return re.search(pattern, raw) is not None
    return (s1_normalize(gold) or "~") in (s1_normalize(raw) or "")


def validate_holdout(doc: dict[str, Any], root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    recs = doc.get("records", [])
    if not 60 <= len(recs) <= 120:
        errors.append(f"record count {len(recs)} outside 60..120")
    ids = [r.get("id") for r in recs]
    if len(set(ids)) != len(ids):
        errors.append("duplicate ids")
    others = other_corpora_raws(root)
    seen_norm: set[str] = set()
    for r in recs:
        rid = r.get("id")
        raw = r.get("raw_address")
        if not isinstance(raw, str) or not raw.strip():
            errors.append(f"{rid}: raw_address missing/blank")
            continue
        if r.get("lang") not in LANGS:
            errors.append(f"{rid}: bad lang {r.get('lang')!r}")
        gold = r.get("gold", {})
        for f, v in gold.items():
            if f not in FIELDS:
                errors.append(f"{rid}: unknown gold field {f}")
            elif not isinstance(v, str) or not v or v != v.strip() or v.lower() in ("null", "none"):
                errors.append(f"{rid}: gold {f} must be a clean non-empty string, got {v!r}")
        for f in r.get("unscored", []):
            if f not in FIELDS:
                errors.append(f"{rid}: unknown unscored field {f}")
        for f, b in r.get("basis", {}).items():
            if b not in ("alias", "inferred"):
                errors.append(f"{rid}: basis {f}={b!r} must be alias|inferred (explicit is the default)")
            if f not in gold:
                errors.append(f"{rid}: basis given for null/absent gold field {f}")
        for f, v in gold.items():
            if f in FIELDS and isinstance(v, str) and f not in ("street_type",) and basis_of(r, f) == "explicit" \
                    and not explicit_in_raw(f, v, raw):
                errors.append(f"{rid}: explicit gold {f}={v!r} not found in raw (annotation inconsistent with policy)")
        norm = s1_normalize(raw) or ""
        if norm in seen_norm:
            errors.append(f"{rid}: duplicate raw within holdout")
        seen_norm.add(norm)
        for name, raws in others.items():
            if norm in raws:
                errors.append(f"{rid}: raw overlaps with {name} (anti-leakage rule)")
    return errors


# ----------------------------------------------------------------------------- predictors
def predict_direct(records: list[dict[str, Any]], root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    from app.parser import parse_batch  # noqa: WPS433 (late import: depends on --root)

    out = parse_batch([{"id": r["id"], "raw_address": r["raw_address"]} for r in records])
    meta = {"mode": "direct", "note": "in-process parse_batch, NOT REST"}
    for rel in ("app/parser.py", "app/geo.py", "data/gazetteer.json"):
        p = root / rel
        if p.exists():
            meta[f"sha256:{rel}"] = file_sha256(p)
    return out, meta


def predict_rest(records: list[dict[str, Any]], url: str, timeout: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    body = json.dumps([{"id": r["id"], "raw_address": r["raw_address"]} for r in records], ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (operator-supplied localhost URL)
        status = resp.status
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError(f"REST response is not a JSON array (HTTP {status})")
    return payload, {"mode": "rest", "url": url, "http_status": status}


# ----------------------------------------------------------------------------- reporting
def pct(x: dict[str, Any]) -> str:
    v = x["value"]
    return "  n/a " if v is None else f"{v * 100:5.1f}% ({x['num']}/{x['den']})"


def text_summary(result: dict[str, Any]) -> str:
    meta, ov = result["meta"], result["overall"]
    lines = [f"!! {DISCLAIMER}", f"holdout {meta["corpus_version"]} n={meta['n_records']} sha256={meta['holdout_sha256'][:16]}… "
             f"mode={meta['predictor']['mode']}", f"S1 rule: {S1_RULE}", ""]
    lines.append(f"{'field':12s} {'acc(norm)':>18s} {'acc(exact)':>18s} {'recall(gold!=null)':>20s} {'null_prec':>16s} {'null_rec':>16s} {'false_fill':>16s}")
    for f in FIELDS:
        pf = ov["per_field"][f]
        tag = "" if f in METRIC_FIELDS else "  [diagnostic]"
        lines.append(f"{f:12s} {pct(pf['accuracy_normalized']):>18s} {pct(pf['accuracy_exact']):>18s} "
                     f"{pct(pf['recall_on_gold_nonnull']):>20s} {pct(pf['null_precision']):>16s} {pct(pf['null_recall']):>16s} {pct(pf['false_fill_rate']):>16s}{tag}")
    lines.append("")
    for name, g in ov["groups"].items():
        m, mn = g["mean_accuracy_normalized"], g["mean_recall_on_gold_nonnull"]
        lines.append(f"group {name:10s} weight {g['weight']:.2f} mean acc(norm) = {_p(m)}  mean recall(gold!=null) = {_p(mn)}  {g['fields']}")
    w, wn = ov["weighted_proxy_normalized"], ov["weighted_proxy_recall_on_gold_nonnull"]
    lines.append(f"weighted proxy (60/25/15, intra-group simple mean): acc(norm)={_p(w)}  recall(gold!=null)={_p(wn)}   <- proxy, NOT official")
    lines.append("  note: acc counts null==null as correct and is inflated by sparse fields; read recall(gold!=null) next to it")
    lines.append(f"record exact match, 9 metric fields (norm): {pct(ov['record_exact_match_metric_fields_normalized'])}; "
                 f"(exact): {pct(ov['record_exact_match_metric_fields_exact'])}")
    lines.append(f"record exact match, all 11 fields (norm):   {pct(ov['record_exact_match_all11_normalized'])}")
    lines.append(f"contract violations in predictions: {result['contract_violations']['records_with_violations']} record(s)")
    lines.append("")
    for title, key in (("by language", "by_lang"), ("by gold-city presence", "by_gold_city_missing"), ("by completeness", "by_completeness")):
        lines.append(f"-- {title}")
        for k, v in result["breakdowns"][key].items():
            lines.append(f"   {k:22s} n={v['n_records']:3d} weighted(acc)={_p(v['weighted_proxy_normalized'])} "
                         f"weighted(recall nn)={_p(v['weighted_proxy_recall_on_gold_nonnull'])} "
                         f"core={_p(v['core'])} details={_p(v['details'])} ref={_p(v['reference'])}")
    lines.append("-- explicit vs alias vs inferred (gold non-null only, pooled metric fields)")
    for k, v in result["breakdowns"]["by_basis_explicit_vs_alias_vs_inferred"].items():
        lines.append(f"   {k:10s} {pct(v['metric_fields_pooled'])}")
    lines.append("")
    lines.append(f"hidden-set quality: {result['hidden_score']}")
    return "\n".join(lines)


def _p(x: float | None) -> str:
    return " n/a " if x is None else f"{x * 100:5.1f}%"


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Proxy holdout evaluation (NOT the official hidden score).")
    ap.add_argument("--holdout", type=Path, default=HOLDOUT_PATH)
    ap.add_argument("--root", type=Path, default=ROOT, help="repo copy that contains app/ (direct mode)")
    ap.add_argument("--url", help="score a running REST endpoint instead of in-process parse_batch")
    ap.add_argument("--predictions", type=Path, help="score a saved JSON array of 14-field outputs aligned with holdout order")
    ap.add_argument("--json-out", type=Path)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--validate", action="store_true", help="only lint the holdout file")
    ap.add_argument("--quiet", action="store_true", help="do not print the JSON to stdout")
    ap.add_argument("--show-mismatches", action="store_true",
                    help="include per-row gold/pred mismatches (QA-internal; do NOT hand to rule authors, see ANNOTATION_POLICY §1)")
    args = ap.parse_args(list(argv) if argv is not None else None)

    doc = load_holdout(args.holdout)
    errors = validate_holdout(doc, args.root)
    if args.validate:
        print(json.dumps({"holdout": str(args.holdout), "n_records": len(doc["records"]), "errors": errors}, ensure_ascii=False, indent=1))
        return 2 if errors else 0
    if errors:
        print("holdout validation FAILED:\n  " + "\n  ".join(errors[:20]), file=sys.stderr)
        return 2

    records = doc["records"]
    if args.predictions:
        preds, pmeta = json.loads(args.predictions.read_text(encoding="utf-8")), {"mode": "saved_predictions", "file": args.predictions.name}
    elif args.url:
        preds, pmeta = predict_rest(records, args.url, args.timeout)
    else:
        preds, pmeta = predict_direct(records, args.root)

    result = {
        "disclaimer": DISCLAIMER,
        "hidden_score": "NOT MEASURED",
        "meta": {"tool": "scripts/eval_holdout.py", "corpus_version": doc["corpus_version"], "n_records": len(records),
                 "holdout_sha256": file_sha256(args.holdout), "s1_rule": S1_RULE,
                 "metric_groups": {k: {"fields": v[0], "weight": v[1]} for k, v in GROUPS.items()},
                 "diagnostic_fields": DIAGNOSTIC_FIELDS, "predictor": pmeta},
        **evaluate(records, preds, args.show_mismatches),
    }
    print(text_summary(result), file=sys.stderr)
    if args.json_out:
        args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
