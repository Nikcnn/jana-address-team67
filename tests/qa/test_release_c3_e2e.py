"""Release C3 regressions through an isolated, real local HTTP server.

Inputs are synthetic. Expectations follow contract v1.1 and the owner-approved
system-failure delta; parser values are compared to the same endpoint, not gold.
"""
from __future__ import annotations

import json
import http.client
import math
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
URL = "/api/v1/addresses/parse-batch"
UPLOAD = "/api/v1/addresses/upload"
KEYS = ("id", "raw_address", "province", "city", "district", "living_area",
        "street_type", "street_name", "house", "apartment", "entrance", "floor",
        "landmark", "confidence")
COMPONENTS = KEYS[2:13]
HEADER = "X-Jana-Invalid-Items"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def _server(tmp_path: Path, *, fault: bool = False):
    port = _free_port()
    harness = tmp_path / ("fault_server.py" if fault else "server.py")
    patch = "\nimport app.parser\ndef explode(record):\n    raise RuntimeError('SYNTHETIC_SYSTEM_FAILURE_SECRET')\napp.parser.parse_address = explode\n" if fault else ""
    harness.write_text(("import uvicorn\n" + patch + "from app.main import app\n" if fault else "import uvicorn\nfrom app.main import app\n") +
                       f"uvicorn.run(app, host='127.0.0.1', port={port}, log_level='critical')\n",
                       encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    stderr_path = tmp_path / "uvicorn.stderr"
    stderr_file = stderr_path.open("wb")
    proc = subprocess.Popen([sys.executable, str(harness)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=stderr_file)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AssertionError(f"isolated uvicorn exited with {proc.returncode}")
            try:
                with urllib.request.urlopen(base + "/health", timeout=0.5) as response:
                    if response.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                time.sleep(0.05)
        else:
            raise AssertionError("isolated uvicorn did not become ready")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        stderr_file.close()


@pytest.fixture
def server(tmp_path):
    with _server(tmp_path) as base:
        yield base


def _request(base: str, path: str, body: bytes, content_type="application/json"):
    req = urllib.request.Request(base + path, data=body,
                                 headers={"Content-Type": content_type}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def _post(base, payload):
    return _request(base, URL, json.dumps(payload, ensure_ascii=True).encode())


def _assert_shape(record):
    assert tuple(record) == KEYS
    assert record["id"] is None or type(record["id"]) in (str, int)
    assert record["raw_address"] is None or isinstance(record["raw_address"], str)
    assert all(record[key] is None or isinstance(record[key], str) for key in COMPONENTS)
    assert type(record["confidence"]) in (int, float)
    assert math.isfinite(record["confidence"]) and 0 <= record["confidence"] <= 1


def _null(identifier=None, raw=None):
    return {"id": identifier, "raw_address": raw, **{key: None for key in COMPONENTS},
            "confidence": 0.0}


def test_real_http_1000_mixed_entries_order_shape_and_parser_reference(server):
    rawset = ["Алматы, ул. Абая 10", "Астана, проспект Республики 21/1", "Ақтөбе, Абай көшесі 7",
              "улица Мира 12а", "ул. Мира 3/2", "", "неизвестная улица Көк-Жайлау 44",
              "\u2003Алматы\nул. Абая 10\r\nкв. 5\u00a0"]
    items = []
    expected_id = []
    expected_raw = []
    null_indexes = set()
    for i in range(1000):
        kind = i % 20
        if kind == 0:
            item = {"id": "duplicate", "raw_address": rawset[(i // 20) % len(rawset)]}
        elif kind == 1:
            item = {"id": "duplicate", "raw_address": rawset[(i // 20 + 1) % len(rawset)]}
        elif kind == 2:
            item = None
        elif kind == 3:
            item = 42
        elif kind == 4:
            item = ["not", "an", "object"]
        elif kind == 5:
            item = {"id": True, "raw_address": rawset[i % len(rawset)]}
        elif kind == 6:
            item = {"id": 1.25, "raw_address": rawset[i % len(rawset)]}
        elif kind == 7:
            item = {"id": 10**100, "raw_address": "ул. Абая 10"}
        elif kind == 8:
            item = {"id": i, "address": "missing raw"}
        elif kind == 9:
            item = {"id": i, "raw_address": 5}
        elif kind == 10:
            item = {"id": i, "raw_address": "\ud800"}
        elif kind == 11:
            item = {"id": "bad\udfff", "raw_address": "ул. Мира 4"}
        elif kind == 12:
            item = {"id": i, "raw_address": "ы" * 3001}
        else:
            item = {"id": i, "raw_address": rawset[i % len(rawset)]}
        items.append(item)
        if isinstance(item, dict):
            ident = item.get("id")
            if isinstance(ident, bool) or not isinstance(ident, (str, int)):
                ident = None
            try:
                if isinstance(ident, str): ident.encode("utf-8")
            except UnicodeEncodeError:
                ident = None
            raw = item.get("raw_address")
            valid_raw = isinstance(raw, str)
            try:
                if valid_raw: raw.encode("utf-8")
            except UnicodeEncodeError:
                valid_raw = False
            if not valid_raw:
                expected_raw.append(None); null_indexes.add(i)
            elif len(raw) > 3000:
                expected_raw.append(raw); null_indexes.add(i)
            else:
                expected_raw.append(raw)
            expected_id.append(ident)
        else:
            expected_id.append(None); expected_raw.append(None); null_indexes.add(i)

    status, headers, content = _post(server, items)
    assert status == 200, content[:300]
    result = json.loads(content)
    assert len(result) == len(items) == 1000
    lowered_headers = {key.lower(): value for key, value in headers.items()}
    assert lowered_headers.get(HEADER.lower()) == str(len(null_indexes))
    for i, record in enumerate(result):
        _assert_shape(record)
        assert record["id"] == expected_id[i]
        assert record["raw_address"] == expected_raw[i]
        if i in null_indexes:
            assert record == _null(expected_id[i], expected_raw[i])
    valid = [i for i in range(1000) if i not in null_indexes]
    reference_status, _, reference_bytes = _post(server, [items[i] for i in valid])
    assert reference_status == 200
    reference = json.loads(reference_bytes)
    assert [result[i] for i in valid] == reference


@pytest.mark.parametrize("wrapper", [None, "items", "records", "addresses"])
def test_real_http_array_and_all_wrappers_mirror_envelope(server, wrapper):
    items = [{"id": 1, "raw_address": "ул. Абая 10"}, None]
    payload = items if wrapper is None else {wrapper: items}
    status, headers, body = _post(server, payload)
    assert status == 200
    data = json.loads(body)
    records = data if wrapper is None else data[wrapper]
    assert len(records) == 2 and records[1] == _null()
    assert {key.lower(): value for key, value in headers.items()}[HEADER.lower()] == "1"


def test_real_http_upload_json_and_jsonl_item_fallback(server):
    rows = [{"id": "x", "raw_address": "Ақтөбе, Абай көшесі 7"}, {"id": 3, "raw_address": None},
            {"id": 4, "raw_address": "z" * 3001}]
    payload = json.dumps(rows, ensure_ascii=True).encode()
    status, headers, body = _request(server, UPLOAD, b"--b\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.json\"\r\nContent-Type: application/json\r\n\r\n" + payload + b"\r\n--b--\r\n", "multipart/form-data; boundary=b")
    assert status == 200
    out = json.loads(body)["items"]
    assert len(out) == 3 and out[1] == _null(3) and out[2] == _null(4, "z" * 3001)
    jsonl = b'\n'.join(json.dumps(item, ensure_ascii=True).encode() for item in rows)
    status, _, body = _request(server, UPLOAD, b"--b\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.jsonl\"\r\nContent-Type: application/json\r\n\r\n" + jsonl + b"\r\n--b--\r\n", "multipart/form-data; boundary=b")
    assert status == 200 and json.loads(body)["items"] == out


@pytest.mark.parametrize("payload,status_code", [(b"{", 400), (b"5", 422), (b'{"unknown":[]}', 422)])
def test_real_http_invalid_json_and_envelope_remain_client_errors(server, payload, status_code):
    status, _, body = _request(server, URL, payload)
    assert status == status_code
    assert b"invalid_json" in body if status_code == 400 else b"invalid_envelope" in body


@pytest.mark.parametrize("size,path,content_type,filename", [
    (32 * 1024 * 1024 + 1, URL, "application/json", ""),
    (8 * 1024 * 1024 + 1, UPLOAD, "application/octet-stream", "large.json"),
])
def test_real_http_body_and_upload_limits_remain_413(server, size, path, content_type, filename):
    if path == URL:
        # The client honors Content-Length and does not transmit the oversized
        # body when the app rejects from its pre-read cap.
        connection = http.client.HTTPConnection(server.split("://", 1)[1], timeout=30)
        connection.putrequest("POST", path)
        connection.putheader("Content-Type", content_type)
        connection.putheader("Content-Length", str(size))
        connection.endheaders()
        response = connection.getresponse()
        status = response.status
        response.read()
        connection.close()
    else:
        boundary = "b"
        prefix = b'--b\r\nContent-Disposition: form-data; name="file"; filename="large.json"\r\n\r\n'
        data = prefix + b" " * size + b"\r\n--b--\r\n"
        status, _, _ = _request(server, path, data, "multipart/form-data; boundary=b")
    assert status == 413


def test_real_http_parser_system_exception_is_5xx_and_does_not_leak_payload(tmp_path):
    secret = "SYNTHETIC_SYSTEM_FAILURE_SECRET"
    with _server(tmp_path, fault=True) as base:
        payload = json.dumps([{"id": 1, "raw_address": "synthetic address " + secret}]).encode()
        status, _, body = _request(base, URL, payload)
        assert status >= 500, f"unexpected HTTP {status}: {body[:200]!r}"
        assert secret.encode() not in body
        assert b"Traceback" not in body and b"RuntimeError" not in body
        upload_body = (b'--b\r\nContent-Disposition: form-data; name="file"; filename="fault.json"\r\n'
                       b'Content-Type: application/json\r\n\r\n' + payload + b'\r\n--b--\r\n')
        upload_status, _, upload_response = _request(
            base, UPLOAD, upload_body, "multipart/form-data; boundary=b")
        assert upload_status >= 500, f"upload unexpectedly returned HTTP {upload_status}"
        assert secret.encode() not in upload_response
        assert b"Traceback" not in upload_response and b"RuntimeError" not in upload_response
    diagnostics = (tmp_path / "uvicorn.stderr").read_bytes()
    assert secret.encode() not in diagnostics