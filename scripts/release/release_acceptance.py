#!/usr/bin/env python3
"""W6 Release acceptance harness (JANA ADDRESS Team 67).

Measures, on the CURRENT candidate in this working copy:
  Phase B: cold startup x N  (timer: docker compose up -d -> /health 200 + real parse request)
  Phase C: REST batch 1000 records x N  (full request->response->validation wall time)
  Phase D: offline/no-egress proof (container run with --network none, probed from inside)

Build/image-load is measured SEPARATELY and never counted as startup.
Input data is synthetic only (data/synthetic_corpus_v1.json). No organizer data.
Output: JSON report (path below) + human summary to stdout.
"""
from __future__ import annotations
import json, os, socket, subprocess, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("JANA_BENCH_OUT", str(ROOT / "artifacts/pitch/benchmark.json")))
with socket.socket() as _socket:
    _socket.bind(("127.0.0.1", 0))
    _port = _socket.getsockname()[1]
ENV = {**os.environ, "JANA_PORT": str(_port), "JANA_BIND": "127.0.0.1"}
BASE = f"http://127.0.0.1:{_port}"
PROJECT = f"jana67-benchmark-{os.getpid()}"
COMPOSE = f"docker compose -p {PROJECT} -f compose.yaml"
NOEGRESS = f"{PROJECT}-noegress"
N_COLD = int(sys.argv[1]) if len(sys.argv) > 1 else 3
N_REST = int(sys.argv[2]) if len(sys.argv) > 2 else 3
IMAGE = "jana-address:team67-rc3-final"


def sh(cmd: str, check=False):
    r = subprocess.run(cmd, shell=True, cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=300)
    if check and r.returncode != 0:
        raise RuntimeError(f"cmd failed: {cmd}\n{r.stdout}\n{r.stderr}")
    return r


def http_json(method, url, payload=None, timeout=300):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return resp.status, json.loads(body.decode("utf-8")), time.perf_counter() - t0


def stats_snapshot():
    cid = sh(COMPOSE + " ps -q jana-address").stdout.strip()
    if not cid:
        return None
    r = sh("docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' " + cid)
    return r.stdout.strip() or None


def wait_health(timeout=180):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        try:
            status, body, _ = http_json("GET", BASE + "/health", timeout=3)
            if status == 200 and body.get("status") == "ok":
                return time.perf_counter() - t0, body
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError("health not ready in time")


def main():
    import os, hashlib
    fx = Path(os.environ.get("REST_FIXTURE", str(ROOT / "data/synthetic_corpus_v1.json")))
    rows = json.loads(fx.read_text(encoding="utf-8"))[:1000]
    assert len(rows) == 1000, len(rows)
    report = {
        "candidate": str(ROOT),
        "host": sh("uname -srmo").stdout.strip() + " / " + sh("nproc").stdout.strip() + " cpu",
        "image": IMAGE,
        "image_inspect": sh(f"docker image inspect {IMAGE} --format '{{{{.Id}}}} {{{{.RepoDigests}}}} {{{{.Size}}}}'").stdout.strip(),
        "build_measured_separately_sec": None,
        "startup_timer_definition": "t0 = BEFORE docker compose up -d; t1 = /health HTTP 200 AND one real parse request through REST succeeded (gazetteer touched); preloaded image, no fresh host/page-cache claim",
        "rest_timer_definition": "t0 = request start; t1 = full response received AND JSON validated (1000 outputs, order, raw/id echo)",
        "cold_start_sec": [], "rest_batch_sec": [], "errors": [],
        "rest_fixture": str(fx), "rest_fixture_sha256": hashlib.sha256(fx.read_bytes()).hexdigest(),
    }

    # --- Phase B + C interleaved: each cold run followed by its REST reps would blur cache;
    # do all cold runs first, then REST reps (container stays up between REST reps).
    for i in range(N_COLD):
        sh(COMPOSE + " down --remove-orphans")
        t0 = time.perf_counter()
        sh(COMPOSE + " up -d --no-build --pull never", check=True)
        hs, health_body = wait_health()
        # real request: parse one record via REST (must exercise the local gazetteer)
        one = [{"id": "w6-probe", "raw_address": "Алматы, Абая 10 кв 5"}]
        st, out, dt = http_json("POST", BASE + "/api/v1/addresses/parse-batch", one)
        total = time.perf_counter() - t0
        ok = st == 200 and isinstance(out, list) and len(out) == 1
        report["cold_start_sec"].append({"run": i + 1, "health_ready_sec": round(hs, 3),
                                         "first_parse_sec": round(dt, 3),
                                         "total_to_ready_sec": round(total, 3),
                                         "probe_ok": ok,
                                         "health": health_body})
        if not ok:
            report["errors"].append(f"cold run {i+1}: probe failed status={st}")

    for i in range(N_REST):
        t0 = time.perf_counter()
        try:
            st, out, dt = http_json("POST", BASE + "/api/v1/addresses/parse-batch", rows, timeout=300)
        except Exception as e:
            report["errors"].append(f"rest run {i+1}: {e!r}")
            continue
        wall = time.perf_counter() - t0
        n_out = len(out) if isinstance(out, list) else -1
        echo_ok = isinstance(out, list) and all(
            o.get("id") == r.get("id") and o.get("raw_address") == r.get("raw_address")
            for o, r in zip(out, rows)) if n_out == 1000 else False
        null_house = sum(1 for o in out if isinstance(o, dict) and o.get("house") is None) if n_out == 1000 else None
        zero_conf = sum(1 for o in out if isinstance(o, dict) and o.get("confidence") == 0.0) if n_out == 1000 else None
        bad_shape = sum(1 for o in out if not (isinstance(o, dict) and len(o) == 14)) if n_out == 1000 else None
        report["rest_batch_sec"].append({
            "run": i + 1, "http": st, "wall_sec": round(wall, 3), "ttfb_plus_body_sec": round(dt, 3),
            "records_in": len(rows), "records_out": n_out, "echo_ok": echo_ok,
            "outputs_not_14_fields": bad_shape, "null_house": null_house,
            "confidence_0_count": zero_conf,
            "stats_after": stats_snapshot()})
        if not (st == 200 and n_out == 1000 and echo_ok):
            report["errors"].append(f"rest run {i+1}: validation failed")

    report["stats_idle"] = stats_snapshot()

    # --- Phase D: no-egress proof
    sh(COMPOSE + " down --remove-orphans")
    r = sh("docker run -d --name " + NOEGRESS + " --network none " + IMAGE)
    if r.returncode == 0:
        time.sleep(3)
        probe = sh("docker exec " + NOEGRESS + " python -c \"import urllib.request,json;"
                   "print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5).read().decode())\"")
        parse_probe = sh("docker exec " + NOEGRESS + " python -c \"import urllib.request,json;"
                         "d=json.dumps([{'id':'x','raw_address':'Алматы, Абая 10 кв 5'}]).encode();"
                         "q=urllib.request.Request('http://127.0.0.1:8000/api/v1/addresses/parse-batch',data=d,"
                         "headers={'Content-Type':'application/json'});"
                         "print(urllib.request.urlopen(q,timeout=10).read().decode()[:200])\"")
        report["no_egress"] = {"container_network": "none", "health_probe_exit": probe.returncode,
                               "health_probe": probe.stdout.strip()[:300],
                               "parse_probe_exit": parse_probe.returncode,
                               "parse_probe": parse_probe.stdout.strip()[:300]}
    else:
        report["no_egress"] = {"error": r.stderr.strip()[:300]}
    sh("docker rm -f " + NOEGRESS)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def agg(key):
        v = [x["total_to_ready_sec"] if "total_to_ready_sec" in x else x["wall_sec"] for x in report[key]]
        return {"n": len(v), "min": round(min(v), 3), "max": round(max(v), 3),
                "p50": round(sorted(v)[len(v)//2], 3)} if v else None
    ck=agg("cold_start_sec"); rk=agg("rest_batch_sec")
    limits={"cold_start_max_lt_180": bool(ck and ck["max"]<180), "rest_1000_max_lt_300": bool(rk and rk["max"]<300)}
    print(json.dumps({"limits":limits,"cold_start": ck, "rest_1000": rk,
                      "errors": report["errors"], "no_egress_ok": report.get("no_egress", {}).get("parse_probe_exit") == 0,
                      "report": str(OUT)}, ensure_ascii=False, indent=2))
    if report["errors"] or not all(limits.values()) or report.get("no_egress", {}).get("parse_probe_exit") != 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
