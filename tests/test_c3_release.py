"""REL-C3/P0 release tests: item-level degradation (C3-v1.1) + system-error boundary (C7-v1.1).

Owner requirement (2026-10-18) overrides ONLY the C7 catch-to-fallback: malformed
items still degrade to one null record each, but an unexpected parser/geo/system
failure must surface as a JSON 5xx with a stable code, never as HTTP 200.
Synthetic fixtures only; no organizer data.
"""
from __future__ import annotations
import json
import logging
import pytest
from fastapi.testclient import TestClient
import app.parser as parser_module
from app.main import app

client = TestClient(app)
BASE = '/api/v1/addresses/parse-batch'
ALIASES = ('/api/parse-batch', '/parse')
UPLOAD = '/api/v1/addresses/upload'
KEYS = ['id', 'raw_address', 'province', 'city', 'district', 'living_area', 'street_type',
        'street_name', 'house', 'apartment', 'entrance', 'floor', 'landmark', 'confidence']
COMPONENT_KEYS = KEYS[2:-1]
SYSTEM_BODY = {'detail': {'code': 'internal_error'}}


@pytest.fixture(autouse=True)
def _reset_error_counter():
    """PARSE_ERROR_COUNT is process-global (/health); keep every test independent."""
    parser_module.PARSE_ERROR_COUNT = 0
    yield
    parser_module.PARSE_ERROR_COUNT = 0


def null_record(identifier=None, raw=None):
    return {key: (identifier if key == 'id' else raw if key == 'raw_address'
                  else 0.0 if key == 'confidence' else None) for key in KEYS}


def expected_id(entry):
    if not isinstance(entry, dict):
        return None
    identifier = entry.get('id')
    if isinstance(identifier, bool) or not isinstance(identifier, (str, int)):
        return None
    if isinstance(identifier, str):
        try:
            identifier.encode('utf-8')
        except UnicodeEncodeError:
            return None
    return identifier


def mixed_entries(n=1000):
    """1000 mixed entries; returns (entries, expected_null_record_count)."""
    entries = []
    nulls = 0
    for i in range(n):
        kind = i % 10
        if kind == 0:
            entries.append({'id': i, 'raw_address': f'город Алматы, улица Абая {i + 10}, дом {i + 11}, кв 2'})
        elif kind == 1:
            entries.append({'id': 'dup', 'raw_address': 'Кулманова 133'})
        elif kind == 2:
            entries.append({'id': i, 'raw_address': '  \r\n\t '})
        elif kind == 3:
            entries.append({'id': i, 'raw_address': 'ЖК Нурсити 2, кв 5 😀\nвторая строка'})
        elif kind == 4:
            entries.append({'id': i, 'raw_address': None}); nulls += 1
        elif kind == 5:
            entries.append({'id': i, 'raw_address': 77}); nulls += 1
        elif kind == 6:
            entries.append(None); nulls += 1
        elif kind == 7:
            entries.append({'id': 'dup', 'raw_address': 'x' * 3001}); nulls += 1
        elif kind == 8:
            entries.append({'id': True, 'raw_address': 'Абай көшесі 5'})
        else:
            entries.append({'id': float(i), 'raw_address': 'a' * 3000})
    return entries, nulls


def test_1000_mixed_entries_item_level_contract():
    entries, expected_nulls = mixed_entries(1000)
    response = client.post(BASE, json=entries)
    assert response.status_code == 200
    assert response.headers.get('X-Jana-Invalid-Items') == str(expected_nulls)
    out = response.json()
    assert isinstance(out, list) and len(out) == 1000
    for index, entry in enumerate(entries):
        record = out[index]
        assert list(record) == KEYS, index
        expected = expected_id(entry)
        if expected is None:
            assert record['id'] is None, index
        else:
            assert record['id'] == expected and type(record['id']) is type(expected), index
        assert record['id'] is not True and not isinstance(record['id'], bool), index
        raw = entry.get('raw_address') if isinstance(entry, dict) else None
        expected_raw = raw if isinstance(raw, str) else None
        assert record['raw_address'] == expected_raw, index
        for key in COMPONENT_KEYS:
            value = record[key]
            assert value is None or (isinstance(value, str) and value != ''), (index, key)
        assert type(record['confidence']) is float and 0.0 <= record['confidence'] <= 1.0, index
    # null records: rules 2-3 of C3-v1.1
    for index, entry in enumerate(entries):
        kind = index % 10
        record = out[index]
        if kind in (4, 5, 6):
            assert record == null_record(expected_id(entry)), index
        elif kind == 7:
            assert record == null_record('dup', 'x' * 3001), index
        elif kind == 2:
            # valid string without content: normal path, echo, zero confidence, no header count
            assert record['raw_address'] == '  \r\n\t ' and record['confidence'] == 0.0, index
        elif kind == 0:
            assert record['city'] == 'Алматы' and record['confidence'] > 0.0, index
            assert record['house'] == str(index + 11), index
        else:
            assert record['confidence'] > 0.0, index  # unknown but valid: quiet low-confidence parse
    # long raw (>3000): preserved verbatim, not parsed, not a global 413
    assert out[7]['raw_address'] == 'x' * 3001 and out[7]['confidence'] == 0.0
    # exact boundary 3000: parsed normally
    assert out[9]['raw_address'] == 'a' * 3000
    # duplicates/order preserved
    assert [record['id'] for record in out[:11]] == [0, 'dup', 2, 3, 4, 5, None, 'dup', None, None, 10]


def test_envelopes_routes_and_upload_contracts():
    row = {'id': 'solo', 'raw_address': 'город Алматы, улица Абая 5, дом 6, кв 2'}
    shapes = [(row, 'single'), ([row], 'array'), ({'items': [row]}, 'items'),
              ({'records': [row]}, 'records'), ({'addresses': [row]}, 'addresses')]
    for body, key in shapes:
        response = client.post(BASE, json=body)
        assert response.status_code == 200, key
        payload = response.json()
        item = payload if key == 'single' else payload[0] if key == 'array' else payload[key][0]
        assert list(item) == KEYS and item['id'] == 'solo'
    for route in ALIASES:
        assert client.post(route, json=[row]).status_code == 200
    json_upload = client.post(UPLOAD, files={'file': ('a.json', json.dumps([row]).encode(), 'application/json')})
    assert json_upload.status_code == 200 and list(json_upload.json()) == ['items']
    assert json_upload.json()['items'][0]['id'] == 'solo'
    wrapped = client.post(UPLOAD, files={'file': ('w.json', json.dumps({'records': [row]}).encode(), 'application/json')})
    assert wrapped.status_code == 200 and list(wrapped.json()) == ['items']  # upload always mirrors to items
    lines = (json.dumps(row) + '\n\n' + json.dumps({**row, 'id': 'two'})).encode()
    jsonl = client.post(UPLOAD, files={'file': ('a.jsonl', lines, 'application/json')})
    assert jsonl.status_code == 200 and [i['id'] for i in jsonl.json()['items']] == ['solo', 'two']


def test_malformed_requests_stay_4xx():
    assert client.post(BASE, content=b'{').status_code == 400
    assert client.post(BASE, json=42).status_code == 422
    assert client.post(BASE, json={'foo': 1}).status_code == 422
    many = [{'id': i, 'raw_address': ''} for i in range(10001)]
    assert client.post(BASE, json=many).status_code == 413
    over = client.post(UPLOAD, files={'file': ('big.json', b' ' * (8 * 1024 * 1024 + 1), 'application/json')})
    assert over.status_code == 413 and over.json()['detail']['code'] == 'upload_too_large'
    bad_line = client.post(UPLOAD, files={'file': ('b.jsonl', b'{"id":1}\nnot-json', 'application/json')})
    assert bad_line.status_code == 400 and bad_line.json()['detail'] == {'code': 'invalid_jsonl', 'line': 2}
    assert 'not-json' not in bad_line.text
    # over-limit items in upload are envelope-level 4xx as well
    assert client.post(UPLOAD, files={'file': ('m.json', json.dumps(many).encode(), 'application/json')}).status_code == 413


def test_unknown_and_malformed_stay_200_failures_do_not(monkeypatch, caplog):
    # baseline: malformed item degrades, unknown valid address parses quietly, no error counted
    baseline = client.post(BASE, json=[{'id': 'u', 'raw_address': '??? ~~ ^^'}, {'id': 'm', 'raw_address': None}])
    assert baseline.status_code == 200 and len(baseline.json()) == 2
    assert baseline.json()[1] == null_record('m', None)
    assert baseline.headers.get('X-Jana-Invalid-Items') == '1'
    assert baseline.json()[0]['confidence'] >= 0.0
    assert parser_module.PARSE_ERROR_COUNT == 0

    def boom(record):
        raise RuntimeError('secret-payload-marker')
    monkeypatch.setattr(parser_module, 'parse_address', boom)
    caplog.set_level(logging.ERROR)
    for route in (BASE, *ALIASES):
        caplog.clear()
        before = parser_module.PARSE_ERROR_COUNT
        # malformed entries around the failing one: still no 200, no partial batch, no malformed header
        response = client.post(route, json=[{'id': None, 'raw_address': None},
                                            {'id': 's1', 'raw_address': 'улица Мира 5'}])
        assert response.status_code == 500, route
        assert response.json() == SYSTEM_BODY, route
        assert 'X-Jana-Invalid-Items' not in response.headers, route
        assert parser_module.PARSE_ERROR_COUNT == before + 1, route
        events = [r for r in caplog.records if r.name == 'app.main' and r.getMessage() == 'parse_system_failure']
        assert len(events) == 1, route
        assert events[0].error_type == 'RuntimeError', route
        for record in caplog.records:
            text = str(record.__dict__)
            assert 'secret-payload-marker' not in text and 'улица Мира 5' not in text, (route, record.name)
    caplog.clear()
    upload = client.post(UPLOAD, files={'file': ('u.json', json.dumps([{'id': 'u1', 'raw_address': 'x'}]).encode(), 'application/json')})
    assert upload.status_code == 500 and upload.json() == SYSTEM_BODY
    assert 'secret-payload-marker' not in upload.text


def test_broken_geo_is_system_failure(monkeypatch):
    def boom():
        raise RuntimeError('geo data unavailable')
    monkeypatch.setattr(parser_module, 'get_data', boom)
    monkeypatch.setattr('app.geo.get_data', boom)
    assert parser_module.PARSE_ERROR_COUNT == 0
    response = client.post(BASE, json=[{'id': 'g', 'raw_address': 'город Алматы, улица Абая 5'}])
    assert response.status_code == 500 and response.json() == SYSTEM_BODY
    assert parser_module.PARSE_ERROR_COUNT == 1


def test_malformed_parser_output_or_length_is_500(monkeypatch):
    from app import main as main_module
    row = {'id': 'p', 'raw_address': 'улица Мира 5'}
    # 1) cardinality violation injected through the function binding used by the API
    monkeypatch.setattr(main_module, 'parse_batch', lambda records: [])
    response = client.post(BASE, json=[row])
    assert response.status_code == 500 and response.json() == SYSTEM_BODY
    assert 'items' not in response.json() and isinstance(response.json(), dict)
    # Restore the real batch binding: each subsequent injection must reach parse_address,
    # not keep passing because the first cardinality-mismatch stub is still installed.
    monkeypatch.setattr(main_module, 'parse_batch', parser_module.parse_batch)
    # 2) wrong confidence type
    good = null_record('p', 'улица Мира 5')
    monkeypatch.setattr(parser_module, 'parse_address', lambda record: {**good, 'confidence': 'high'})
    assert client.post(BASE, json=[row]).status_code == 500
    # 3) empty component string
    monkeypatch.setattr(parser_module, 'parse_address', lambda record: {**good, 'street_name': ''})
    assert client.post(BASE, json=[row]).status_code == 500
    # 4) non-dict result
    monkeypatch.setattr(parser_module, 'parse_address', lambda record: 'not-a-dict')
    assert client.post(BASE, json=[row]).status_code == 500
    # 5) missing output key
    missing = {key: value for key, value in good.items() if key != 'district'}
    monkeypatch.setattr(parser_module, 'parse_address', lambda record: missing)
    assert client.post(BASE, json=[row]).status_code == 500
    # each injected failure counted exactly once at the boundary
    assert parser_module.PARSE_ERROR_COUNT == 5
    # malformed-only batches remain a valid empty parse (no valid items -> nothing to validate)
    monkeypatch.setattr(parser_module, 'parse_address', lambda record: (_ for _ in ()).throw(RuntimeError('never called')))
    ok = client.post(BASE, json=[{'id': 'm', 'raw_address': None}])
    assert ok.status_code == 200 and ok.headers.get('X-Jana-Invalid-Items') == '1'
    assert parser_module.PARSE_ERROR_COUNT == 5


def test_error_counter_and_health_semantics():
    assert client.get('/health').json()['error_count'] == 0
    before = client.get('/health').json()['error_count']

    def boom(record):
        raise RuntimeError('synthetic')
    import app.parser as pm
    original = pm.parse_address
    try:
        pm.parse_address = boom
        for _ in range(2):
            assert client.post(BASE, json=[{'id': 'e', 'raw_address': 'улица Мира 5'}]).status_code == 500
        assert client.get('/health').json()['error_count'] == before + 2
    finally:
        pm.parse_address = original
    assert client.post(BASE, json=[{'id': 'ok', 'raw_address': 'улица Мира 5'}]).status_code == 200
    assert client.get('/health').json()['error_count'] == before + 2
