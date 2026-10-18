#!/usr/bin/env python
"""Performance harness: direct parse_batch AND real HTTP (uvicorn on 127.0.0.1), plus cold-start method.

Modes (all labelled in the output; never mix them up when quoting numbers):
  direct : in-process parse_batch(records). NOT REST, no JSON/HTTP/serialisation cost.
  rest   : a real `python -m uvicorn app.main:app` subprocess; requests over loopback TCP.
  cold   : server start-up timeline, repeated; see COLD_START_METHOD.

Input: deterministic synthetic corpus (default data/synthetic_corpus_v1.json[:N], same rows as scripts/benchmark.py);
the SHA-256 of the exact JSON body sent is printed so a later run can prove it used the same input.
This is a speed measurement only: it says NOTHING about accuracy. Numbers are valid for this machine/load only.

Percentiles: with N runs of a 1000-record batch, p95 of N<20 samples is just "close to the max". For a meaningful
tail the REST mode also records `--single` sequential one-record requests (default 200) and reports their p50/p95/max.

Usage (cwd = repo copy):
  python scripts/qa_perf.py --mode direct
  python scripts/qa_perf.py --mode rest --runs 5
  python scripts/qa_perf.py --mode all --json-out perf.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CORPUS_FILE = "data/synthetic_corpus_v1.json"
DISCLAIMER = "speed only, NOT accuracy; valid for this machine and load at measurement time"

COLD_START_METHOD = {
    "command": "python -m uvicorn app.main:app --host 127.0.0.1 --port <free> --log-level warning  (cwd = repo root)",
    "t0": "immediately before subprocess.Popen",
    "t_listening": "first successful TCP connect to the port",
    "t_health": "first HTTP 200 from GET /health (app imported, gazetteer loaded by the health handler)",
    "t_first_parse": "first HTTP 200 from POST /api/v1/addresses/parse-batch with ONE record (includes any lazy init)",
    "definition_of_start": "t_first_parse - t0 (service is useful), reported next to t_health - t0",
    "caveats": [
        "OS page cache is NOT dropped (needs root): 'cold' = fresh process, warm file cache. Disk-cold start would be slower.",
        "Docker container start / image pull are NOT included (docker not used by this script).",
        "Python bytecode cache disabled by PYTHONDONTWRITEBYTECODE=1 for reproducibility, so imports are compiled each time (pessimistic).",
    ],
}


# ----------------------------------------------------------------------------- statistics
def pctl(values: list[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100). Deterministic, no interpolation."""
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(-(-q * len(s) // 100)) - 1))
    return s[k]


def dist(values: list[float], unit: str = "ms") -> dict[str, Any]:
    return {"n": len(values), "unit": unit, "min": round(min(values), 3), "p50": round(pctl(values, 50), 3),
            "p95": round(pctl(values, 95), 3), "max": round(max(values), 3), "mean": round(statistics.fmean(values), 3),
            "samples": [round(v, 3) for v in values] if len(values) <= 20 else None}


# ----------------------------------------------------------------------------- corpus
def load_corpus(root: Path, n: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = json.loads((root / CORPUS_FILE).read_text(encoding="utf-8"))
    recs = [{"id": r["id"], "raw_address": r["raw_address"]} for r in rows[:n]]
    if len(recs) < n:
        raise SystemExit(f"corpus has only {len(recs)} rows, need {n}")
    body = json.dumps(recs, ensure_ascii=False).encode("utf-8")
    return recs, {"source": CORPUS_FILE, "records": len(recs), "body_bytes": len(body),
                  "input_sha256": hashlib.sha256(body).hexdigest(),
                  "distinct_raw": len({r["raw_address"] for r in recs}),
                  "note": "synthetic repeated stress rows (not real data, not accuracy data)"}


def environment() -> dict[str, Any]:
    load = os.getloadavg()
    return {"python": sys.version.split()[0], "platform": platform.platform(), "cpus": os.cpu_count(),
            "loadavg_1_5_15": [round(x, 2) for x in load],
            "mem_available_mb": _meminfo_mb("MemAvailable"),
            "note": "shared VM: other processes run concurrently; treat as indicative"}


def _meminfo_mb(key: str) -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith(key + ":"):
                return int(line.split()[1]) // 1024
    except OSError:
        return None
    return None


# ----------------------------------------------------------------------------- direct
def run_direct(root: Path, recs: list[dict[str, Any]], runs: int) -> dict[str, Any]:
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    from app.parser import parse_address, parse_batch  # noqa: WPS433

    parse_batch(recs[:50])  # warm-up (compiled regex cache, gazetteer load); excluded from samples
    batch_ms: list[float] = []
    for _ in range(runs):
        t = time.perf_counter()
        out = parse_batch(recs)
        batch_ms.append((time.perf_counter() - t) * 1000)
        assert len(out) == len(recs)
    per_record_ms: list[float] = []
    for r in recs:
        t = time.perf_counter()
        parse_address(r)
        per_record_ms.append((time.perf_counter() - t) * 1000)
    b = dist(batch_ms)
    return {
        "mode": "direct", "label": "прямой parse_batch, НЕ REST (no HTTP, no JSON encode/decode)",
        "runs": runs, "records_per_run": len(recs),
        "batch_latency_ms": b,
        "throughput_records_per_s_at_p50": round(len(recs) / (b["p50"] / 1000), 1),
        "per_record_latency_ms": dist(per_record_ms),
        "peak_rss_mb_this_process": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "peak_rss_note": "ru_maxrss of the measuring process (includes interpreter + imports)",
    }


# ----------------------------------------------------------------------------- rest helpers
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(root: Path) -> tuple[subprocess.Popen, str, float]:
    port = free_port()
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    t0 = time.perf_counter()
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port),
                             "--log-level", "warning"], cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return proc, f"http://127.0.0.1:{port}", t0


def stop_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def http(method: str, url: str, body: bytes | None = None, timeout: float = 120.0) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers={"Content-Type": "application/json"} if body else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def peak_rss_mb(pid: int) -> dict[str, float | None]:
    out: dict[str, float | None] = {"VmHWM_mb": None, "VmRSS_mb": None}
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            for key in ("VmHWM", "VmRSS"):
                if line.startswith(key + ":"):
                    out[key + "_mb"] = round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    return out


def cold_start_once(root: Path, probe: bytes, startup_timeout: float = 60.0) -> dict[str, Any]:
    proc, base, t0 = start_server(root)
    host, port = "127.0.0.1", int(base.rsplit(":", 1)[1])
    marks: dict[str, float] = {}
    try:
        deadline = time.perf_counter() + startup_timeout
        while time.perf_counter() < deadline and len(marks) < 3:
            if proc.poll() is not None:
                return {"error": f"server exited (code {proc.returncode}): {(proc.stderr.read() or b'')[-200:]!r}"}
            try:
                if "listening" not in marks:
                    socket.create_connection((host, port), timeout=0.2).close()
                    marks["listening"] = time.perf_counter()
                if "health" not in marks and http("GET", base + "/health", timeout=2)[0] == 200:
                    marks["health"] = time.perf_counter()
                if "health" in marks and "first_parse" not in marks:
                    st, _ = http("POST", base + "/api/v1/addresses/parse-batch", probe, timeout=10)
                    if st == 200:
                        marks["first_parse"] = time.perf_counter()
            except OSError:
                time.sleep(0.02)
        if len(marks) < 3:
            return {"error": f"startup incomplete after {startup_timeout}s: reached {sorted(marks)}"}
        return {"t_listening_ms": round((marks["listening"] - t0) * 1000, 1), "t_health_ms": round((marks["health"] - t0) * 1000, 1),
                "t_first_parse_ms": round((marks["first_parse"] - t0) * 1000, 1), **peak_rss_mb(proc.pid)}
    finally:
        stop_server(proc)


# ----------------------------------------------------------------------------- rest
def run_rest(root: Path, recs: list[dict[str, Any]], runs: int, single: int) -> dict[str, Any]:
    body = json.dumps(recs, ensure_ascii=False).encode("utf-8")
    proc, base, _ = start_server(root)
    try:
        deadline = time.time() + 60
        while True:
            if proc.poll() is not None:
                return {"mode": "rest", "status": "NOT RUN", "reason": f"uvicorn exited (code {proc.returncode}): {(proc.stderr.read() or b'')[-300:]!r}"}
            try:
                if http("GET", base + "/health", timeout=2)[0] == 200:
                    break
            except OSError:
                if time.time() > deadline:
                    return {"mode": "rest", "status": "NOT RUN", "reason": "server did not become healthy in 60s"}
                time.sleep(0.1)
        st, _ = http("POST", base + "/api/v1/addresses/parse-batch", json.dumps(recs[:50], ensure_ascii=False).encode())
        if st != 200:
            return {"mode": "rest", "status": "NOT RUN", "reason": f"warm-up request failed: HTTP {st}"}
        batch_ms, statuses, resp_bytes = [], [], 0
        for _ in range(runs):
            t = time.perf_counter()
            st, data = http("POST", base + "/api/v1/addresses/parse-batch", body)
            batch_ms.append((time.perf_counter() - t) * 1000)
            statuses.append(st)
            resp_bytes = len(data)
            if st == 200:
                parsed = json.loads(data)
                if len(parsed) != len(recs):
                    return {"mode": "rest", "status": "FAIL", "reason": f"cardinality {len(parsed)} != {len(recs)}"}
        single_ms = []
        one = [json.dumps([r], ensure_ascii=False).encode() for r in recs[:single]]
        for payload in one:
            t = time.perf_counter()
            st, _ = http("POST", base + "/api/v1/addresses/parse-batch", payload, timeout=30)
            single_ms.append((time.perf_counter() - t) * 1000)
            statuses.append(st)
        b = dist(batch_ms)
        return {
            "mode": "rest", "status": "OK" if set(statuses) == {200} else "FAIL", "label": "real HTTP over loopback to uvicorn (1 process)",
            "http_statuses_seen": sorted(set(statuses)), "runs": runs, "records_per_run": len(recs), "response_bytes_per_run": resp_bytes,
            "batch_latency_ms": b, "throughput_records_per_s_at_p50": round(len(recs) / (b["p50"] / 1000), 1),
            "single_record_latency_ms": dist(single_ms), "single_requests": len(single_ms),
            "server_memory": peak_rss_mb(proc.pid), "server_memory_note": "VmHWM = peak RSS of the uvicorn process over the whole session",
        }
    finally:
        stop_server(proc)


def run_cold(root: Path, repeats: int) -> dict[str, Any]:
    probe = json.dumps([{"id": "cold", "raw_address": "ул. Абая 10, кв 5"}], ensure_ascii=False).encode()
    shots = [cold_start_once(root, probe) for _ in range(repeats)]
    ok = [s for s in shots if "error" not in s]
    out: dict[str, Any] = {"mode": "cold", "repeats": repeats, "method": COLD_START_METHOD, "shots": shots}
    if ok:
        for k in ("t_listening_ms", "t_health_ms", "t_first_parse_ms"):
            out[k] = dist([s[k] for s in ok], "ms")
    else:
        out["status"] = "NOT RUN"
    # python import cost alone, for attribution
    code = "import time,sys;t=time.perf_counter();sys.path.insert(0,'.');import app.main;print((time.perf_counter()-t)*1000)"
    imp = []
    for _ in range(repeats):
        r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=60,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, check=False)
        if r.returncode == 0:
            imp.append(float(r.stdout.strip().splitlines()[-1]))
    out["import_app_main_ms"] = dist(imp) if imp else None
    return out


# ----------------------------------------------------------------------------- reporting
def summary(rep: dict[str, Any]) -> str:
    c = rep["corpus"]
    lines = [f"QA PERF  ({DISCLAIMER})", f"input: {c['records']} records, {c['body_bytes']} bytes, sha256={c['input_sha256'][:16]}…  distinct_raw={c['distinct_raw']}",
             f"env: python {rep['environment']['python']}, cpus={rep['environment']['cpus']}, loadavg={rep['environment']['loadavg_1_5_15']}", ""]
    d = rep.get("direct")
    if d:
        b, p = d["batch_latency_ms"], d["per_record_latency_ms"]
        lines += [f"[direct] {d['label']}", f"  batch of {d['records_per_run']} x{d['runs']}: p50={b['p50']}ms p95={b['p95']}ms max={b['max']}ms -> {d['throughput_records_per_s_at_p50']} rec/s",
                  f"  per-record (n={p['n']}): p50={p['p50']}ms p95={p['p95']}ms max={p['max']}ms", f"  peak RSS (measuring proc): {d['peak_rss_mb_this_process']} MB"]
    r = rep.get("rest")
    if r:
        if r.get("status") not in ("OK",):
            lines.append(f"[rest] {r.get('status')}: {r.get('reason', r.get('http_statuses_seen'))}")
        else:
            b, s = r["batch_latency_ms"], r["single_record_latency_ms"]
            lines += [f"[rest] {r['label']}", f"  batch of {r['records_per_run']} x{r['runs']}: p50={b['p50']}ms p95={b['p95']}ms max={b['max']}ms -> {r['throughput_records_per_s_at_p50']} rec/s (response {r['response_bytes_per_run']} B)",
                      f"  single-record POSTs (n={s['n']}): p50={s['p50']}ms p95={s['p95']}ms max={s['max']}ms",
                      f"  server peak RSS: {r['server_memory']['VmHWM_mb']} MB (VmHWM)"]
    cs = rep.get("cold")
    if cs:
        if "t_first_parse_ms" in cs:
            lines.append(f"[cold] x{cs['repeats']}: listening p50={cs['t_listening_ms']['p50']}ms, /health p50={cs['t_health_ms']['p50']}ms, first parse p50={cs['t_first_parse_ms']['p50']}ms (max {cs['t_first_parse_ms']['max']}ms)")
            if cs.get("import_app_main_ms"):
                lines.append(f"  import app.main alone: p50={cs['import_app_main_ms']['p50']}ms")
        else:
            lines.append(f"[cold] NOT RUN: {cs.get('shots')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="QA performance harness (direct / REST / cold-start).")
    ap.add_argument("--mode", choices=["direct", "rest", "cold", "all"], default="all")
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--records", type=int, default=1000)
    ap.add_argument("--runs", type=int, default=5, help="repetitions of the full batch (>=3)")
    ap.add_argument("--single", type=int, default=200, help="sequential single-record REST requests for tail latency")
    ap.add_argument("--cold-repeats", type=int, default=3)
    ap.add_argument("--json-out", type=Path)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if args.runs < 3 or args.cold_repeats < 3:
        ap.error("--runs and --cold-repeats must be >= 3")
    root = args.root.resolve()
    recs, corpus = load_corpus(root, args.records)
    rep: dict[str, Any] = {"tool": "scripts/qa_perf.py", "disclaimer": DISCLAIMER, "environment": environment(), "corpus": corpus}
    if args.mode in ("direct", "all"):
        rep["direct"] = run_direct(root, recs, args.runs)
    if args.mode in ("rest", "all"):
        rep["rest"] = run_rest(root, recs, args.runs, args.single)
    if args.mode in ("cold", "all"):
        rep["cold"] = run_cold(root, args.cold_repeats)
    print(summary(rep), file=sys.stderr)
    if args.json_out:
        args.json_out.write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if not args.quiet:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    failed = [k for k in ("rest",) if k in rep and rep[k].get("status") not in ("OK", None)]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
