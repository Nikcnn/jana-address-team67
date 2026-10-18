"""Determinism, restart-stability, statelessness and no-egress (contract C7 «Determinism»).

Subprocess tests start a FRESH interpreter (cold caches) with different PYTHONHASHSEED values.
"""
from __future__ import annotations

import copy
import hashlib
import json
import socket
import threading

import pytest

from .helpers import components, corpus_all_safe, parse_one, run_python

DIGEST_CODE = (
    "import json,hashlib,sys; sys.path.insert(0,'.');"
    "from app.parser import parse_batch;"
    "rows=json.load(open('data/qa/regression_corpus_v1.json',encoding='utf-8'))['records'];"
    "rows=[{'id':r['id'],'raw_address':r['raw_address']} for r in rows]+json.load(open('data/demo_input.json',encoding='utf-8'));"
    "out=parse_batch(rows);"
    "print(hashlib.sha256(json.dumps(out,ensure_ascii=False).encode()).hexdigest())"
)


def test_same_input_twice_in_one_process_is_identical():
    from app.parser import parse_batch
    recs = [{"id": i, "raw_address": r} for i, r in enumerate(corpus_all_safe()[:300])]
    assert parse_batch(recs) == parse_batch(recs)


@pytest.mark.parametrize("seed", ["0", "1", "12345", "random"])
def test_restart_gives_identical_output_across_hash_seeds(seed):
    """Fresh interpreter + different hash seed (set/dict ordering) → byte-identical output digest."""
    ref = run_python(DIGEST_CODE, env_extra={"PYTHONHASHSEED": "0"}, timeout=120)
    assert ref.returncode == 0, ref.stderr[-500:]
    cur = run_python(DIGEST_CODE, env_extra={"PYTHONHASHSEED": seed}, timeout=120)
    assert cur.returncode == 0, cur.stderr[-500:]
    assert cur.stdout.strip() == ref.stdout.strip() and len(ref.stdout.strip()) == 64


def test_parse_does_not_mutate_its_input():
    from app.parser import parse_batch
    recs = [{"id": i, "raw_address": r, "extra": {"k": [1, 2]}} for i, r in enumerate(corpus_all_safe()[:200])]
    snapshot = copy.deepcopy(recs)
    parse_batch(recs)
    assert recs == snapshot


def test_output_records_are_independent_objects():
    """Mutating one result must not change another (no shared dict returned from a cache)."""
    from app.parser import parse_batch
    out = parse_batch([{"id": 1, "raw_address": "ул. Мира 5"}] * 3)
    out[0]["house"] = "MUTATED"
    assert out[1]["house"] == "5" and out[2]["house"] == "5"
    again = parse_batch([{"id": 1, "raw_address": "ул. Мира 5"}])[0]
    assert again["house"] == "5", "a result mutated by a caller leaked into later parses (cache by raw_address?)"


def test_call_order_does_not_affect_results():
    """No hidden state: forward vs backward single-record calls agree."""
    raws = corpus_all_safe()[:150]
    fwd = [components(parse_one(r)) for r in raws]
    bwd = [components(parse_one(r)) for r in reversed(raws)][::-1]
    assert fwd == bwd


def test_concurrent_parsing_is_consistent():
    from app.parser import parse_batch
    raws = corpus_all_safe()[:200]
    recs = [{"id": i, "raw_address": r} for i, r in enumerate(raws)]
    expected = parse_batch(recs)
    results: list = [None] * 6

    def work(k: int) -> None:
        results[k] = parse_batch(recs)

    threads = [threading.Thread(target=work, args=(k,)) for k in range(6)]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    assert all(r == expected for r in results)


def test_runtime_makes_no_network_calls(monkeypatch):
    """Any attempt to open a socket during parse/import-free runtime fails the test."""
    from app.parser import parse_batch

    def forbidden(*a, **k):
        raise AssertionError("network access attempted at runtime")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    out = parse_batch([{"id": i, "raw_address": r} for i, r in enumerate(corpus_all_safe()[:200])])
    assert len(out) == 200


def test_ids_do_not_participate_in_parsing_under_random_assignment():
    import random
    from app.parser import parse_batch
    raws = corpus_all_safe()[:150]
    rng = random.Random(99)
    base = [components(o) for o in parse_batch([{"id": i, "raw_address": r} for i, r in enumerate(raws)])]
    for _ in range(3):
        ids = [rng.choice([rng.randint(0, 10**9), f"x{rng.random()}", ""]) for _ in raws]
        got = [components(o) for o in parse_batch([{"id": i, "raw_address": r} for i, r in zip(ids, raws)])]
        assert got == base


def test_gazetteer_file_is_unchanged_by_running_the_parser():
    from pathlib import Path
    p = Path(__file__).resolve().parents[2] / "data" / "gazetteer.json"
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    from app.parser import parse_batch
    parse_batch([{"id": i, "raw_address": r} for i, r in enumerate(corpus_all_safe()[:100])])
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before


# ----------------------------------------------------------------------------- real server restart (REST)
@pytest.mark.rest
def test_rest_restart_determinism_with_real_uvicorn():
    """Two separate uvicorn processes (restart) answer the same request with byte-identical JSON."""
    from .server import running_server, post_json
    items = [{"id": f"r{i}", "raw_address": r} for i, r in enumerate(corpus_all_safe()[:120])]
    bodies = []
    for _ in range(2):
        with running_server() as base:
            status, body = post_json(f"{base}/api/v1/addresses/parse-batch", items)
            assert status == 200, status
            bodies.append(body)
    assert bodies[0] == bodies[1] and json.loads(bodies[0])[0]["id"] == "r0"
