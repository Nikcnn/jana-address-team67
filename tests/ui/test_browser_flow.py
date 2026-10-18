"""Real Chrome + uvicorn end-to-end, using only stdlib and Node built-ins.

Requires Node >=22 (native WebSocket) and Chrome/Chromium. Missing prerequisites
are a failure, not a silently skipped verification. No package installs needed.
"""
import csv
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

from fastapi.testclient import TestClient
from app.main import app

ROOT = Path(__file__).resolve().parents[2]
FIELDS = ["id", "raw_address", "province", "city", "district", "living_area",
          "street_type", "street_name", "house", "apartment", "entrance", "floor",
          "landmark", "confidence"]


def test_browser_flow(tmp_path):
    node = shutil.which("node")
    chrome = os.environ.get("CHROME_BIN") or next(
        (path for name in ("google-chrome", "chromium", "chromium-browser")
         if (path := shutil.which(name))), None)
    assert node and chrome, "UI E2E requires Node >=22 and Chrome/Chromium (or CHROME_BIN)"
    records = [
        {"id": "dup", "raw_address": '  Қарағанды, көшесі "Әліби" 12А/1, кв 05\r\n<img src=x onerror="window.__xss=1">  '},
        {"id": "dup", "raw_address": ""},
        {"id": 0, "raw_address": "ҰӨҚҒӘҢІҮҺ" * 300},
        {"id": 900719925474099312345678901, "raw_address": "Сынақ көшесі 5Б/2"},
    ]
    original = TestClient(app).post('/api/v1/addresses/parse-batch', json=records).json()
    assert len(original) == len(records)
    fixtures = {
        "sample.json": '\ufeff' + json.dumps({"items": records}, ensure_ascii=False),
        "sample.jsonl": '\ufeff' + '\n\n'.join(json.dumps(row, ensure_ascii=False) for row in records) + '\n',
        "bad.jsonl": '{broken JSON',
        "empty.json": '[]',
    }
    for name, content in fixtures.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    (tmp_path / 'expected.json').write_text(json.dumps(original, ensure_ascii=False), encoding='utf-8')
    formula_cases = [
        ('=1+1', "'=1+1"), ('+7', "'+7"), ('@SUM(A1)', "'@SUM(A1)"),
        ('-1+2', "'-1+2"), ('\tx', "'\tx"), ('\rx', "'\rx"),
        ('-1', '-1'), ('13Б', '13Б'), ('100/2', '100/2'), ('Мира', 'Мира'),
        ('12а', '12а'), ('', ''), (None, ''), ('-001', '-001'), ('-0', '-0'),
        ('-1\n', "'-1\n"), ('-1\r', "'-1\r"), ('-1\r\n', "'-1\r\n"),
        ('-1.5', "'-1.5"), ('--1', "'--1"), ('-١', "'-١"),
        ('=SUM("a", "b")\r\n', "'=SUM(\"a\", \"b\")\r\n"),
        ("'=1+1", "'=1+1"), (' =1+1', ' =1+1'), ('\nx', '\nx'),
    ]
    formula_rows = [
        {key: (0.5 if key == 'confidence' else
               (value or '') if key in ('id', 'raw_address') else value or None)
         for key in FIELDS}
        for value, _ in formula_cases
    ]
    (tmp_path / 'formula-input.json').write_text(
        json.dumps(formula_rows, ensure_ascii=False), encoding='utf-8')
    def null_row(identifier, raw_address):
        return {key: identifier if key == 'id' else raw_address if key == 'raw_address'
                else 0.0 if key == 'confidence' else None for key in FIELDS}

    mixed_rows = [
        {**null_row('valid', 'Тестоград, улица Мира 12а'), 'city': 'Тестоград', 'confidence': 0.8},
        {**null_row(None, 'Тестоград, улица Мира 12а'), 'city': 'Тестоград', 'confidence': 0.8},
        null_row('dup', None), null_row(None, None), null_row(None, None),
        null_row('empty', ''), null_row('long', 'Ә' * 3001),
        {**null_row(None, 'Синтетический адрес'), 'confidence': 0.2},
    ]
    (tmp_path / 'mixed-response.json').write_text(json.dumps(mixed_rows, ensure_ascii=False), encoding='utf-8')
    (tmp_path / 'mixed.json').write_text(json.dumps({'items': [
        {'id': row['id'], 'raw_address': row['raw_address']} for row in mixed_rows
    ]}, ensure_ascii=False), encoding='utf-8')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    server = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1',
         '--port', str(port), '--no-access-log', '--log-level', 'warning'],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            assert server.poll() is None, 'uvicorn exited during startup'
            try:
                with urlopen(base + '/health', timeout=1) as response:
                    assert response.status == 200
                break
            except OSError:
                time.sleep(.05)
        else:
            raise AssertionError('uvicorn did not become healthy')
        result = subprocess.run(
            [node, str(Path(__file__).with_name('browser_flow.mjs')), base, chrome, str(tmp_path)],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=100)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        server.terminate()
        server.communicate(timeout=10)
    baseline = json.loads((tmp_path / 'baseline.json').read_text())
    assert baseline == original
    corrected = json.loads((tmp_path / 'corrected.json').read_text())
    assert len(corrected) == 4
    assert all(list(row) == FIELDS for row in corrected)
    assert corrected[0] == original[0]  # Duplicate ID row was not edited.
    assert corrected[1]['house'] == '12Ә/1'
    assert corrected[1]['landmark'] == '  Ә, "Қақпа"  '
    assert corrected[1]['apartment'] is None
    assert corrected[1]['confidence'] == original[1]['confidence']
    assert [row['id'] for row in corrected] == [row['id'] for row in records]
    assert [row['raw_address'] for row in corrected] == [row['raw_address'] for row in records]
    assert corrected[3]['id'] == 900719925474099312345678901
    csv_bytes = (tmp_path / 'corrected.csv').read_bytes()
    assert csv_bytes.startswith(b'\xef\xbb\xbf')
    csv_rows = list(csv.reader(io.StringIO(csv_bytes.decode('utf-8-sig'), newline='')))
    assert csv_rows[0] == FIELDS
    assert csv_rows[1:] == [["" if row[key] is None else str(row[key]) for key in FIELDS] for row in corrected]
    assert json.loads((tmp_path / 'reset.json').read_text()) == original
    assert json.loads((tmp_path / 'jsonl.json').read_text()) == original
    assert json.loads((tmp_path / 'empty.json.export').read_text()) == []
    # Check exact JSON bytes, not just equality after parsing: CSV must not mutate state.
    expected_json = '[\n' + ',\n'.join(
        '  ' + json.dumps(row, ensure_ascii=False) for row in formula_rows) + '\n]\n'
    assert (tmp_path / 'formula-before.json').read_bytes() == expected_json.encode('utf-8')
    assert (tmp_path / 'formula-after.json').read_bytes() == expected_json.encode('utf-8')
    expected_csv_rows = [FIELDS] + [
        ['0.5' if key == 'confidence' else guarded for key in FIELDS]
        for _, guarded in formula_cases
    ]
    formula_csv = (tmp_path / 'formula.csv').read_bytes()
    assert list(csv.reader(io.StringIO(formula_csv.decode('utf-8-sig'), newline=''))) == expected_csv_rows
    output = io.StringIO(newline='')
    csv.writer(output, quoting=csv.QUOTE_ALL, lineterminator='\r\n').writerows(expected_csv_rows)
    assert formula_csv == ('\ufeff' + output.getvalue()).encode('utf-8')
    assert json.loads((tmp_path / 'mixed-baseline.json').read_text()) == mixed_rows
    mixed_edited = json.loads((tmp_path / 'mixed-edited.json').read_text())
    expected_edited = [dict(row) for row in mixed_rows]
    expected_edited[3].update(house='=1+1', floor='-1')
    assert mixed_edited == expected_edited
    assert json.loads((tmp_path / 'mixed-reset.json').read_text()) == mixed_rows
    assert json.loads((tmp_path / 'mixed-rejected.json').read_text()) == mixed_rows
    mixed_csv = (tmp_path / 'mixed-edited.csv').read_bytes()
    expected_mixed_csv = [FIELDS] + [
        ['' if row[key] is None else "'=1+1" if row[key] == '=1+1' else str(row[key])
         for key in FIELDS] for row in expected_edited
    ]
    # Browser number serialization writes 0, not Python's 0.0.
    for row, expected in zip(expected_edited, expected_mixed_csv[1:]):
        expected[-1] = str(row['confidence']).removesuffix('.0')
    assert list(csv.reader(io.StringIO(mixed_csv.decode('utf-8-sig'), newline=''))) == expected_mixed_csv
    mixed_json_text = (tmp_path / 'mixed-edited.json').read_text()
    assert '"id": null' in mixed_json_text and '"raw_address": null' in mixed_json_text
    print(result.stdout)
