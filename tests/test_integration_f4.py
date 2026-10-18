"""C7 release delta: internal parser failures are not successful blank records.

Malformed inputs are handled separately by the transport's item-level C3 path.
Only the HTTP boundary logs/counts unexpected system failures; parser stays
transport-independent and preserves the original cause by propagating it.
"""
import pytest
import app.parser as parser_module
from app.parser import parse_batch


def test_systemic_parser_error_propagates_without_blank_record(monkeypatch):
    failure = RuntimeError('synthetic systemic failure')
    def boom(record):
        raise failure
    monkeypatch.setattr(parser_module, 'parse_address', boom)
    before = parser_module.PARSE_ERROR_COUNT
    with pytest.raises(RuntimeError) as caught:
        parse_batch([{'id': 'a', 'raw_address': 'synthetic payload marker'}])
    assert caught.value is failure
    assert parser_module.PARSE_ERROR_COUNT == before  # HTTP boundary owns this metric


def test_health_reports_parser_error_counter():
    from fastapi.testclient import TestClient
    from app.main import app
    body = TestClient(app).get('/health').json()
    assert body['error_count'] == parser_module.PARSE_ERROR_COUNT
    assert body['ready'] is True


def test_unparseable_valid_string_does_not_increment_error_counter():
    before = parser_module.PARSE_ERROR_COUNT
    out = parse_batch([{'id': 'a', 'raw_address': '??? ~~ ^^'}])
    assert len(out) == 1 and out[0]['house'] is None
    assert parser_module.PARSE_ERROR_COUNT == before


def test_http_api_distinguishes_service_failure_from_unparsed_address(monkeypatch, caplog):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    health_before = client.get('/health').json()['error_count']
    response = client.post('/api/v1/addresses/parse-batch',
                           json=[{'id': 'u1', 'raw_address': '??? ~~ ^^'}])
    assert response.status_code == 200 and response.json()[0]['house'] is None
    assert client.get('/health').json()['error_count'] == health_before
    def boom(record):
        raise RuntimeError('synthetic exception secret')
    monkeypatch.setattr(parser_module, 'parse_address', boom)
    with caplog.at_level('ERROR', logger='app.main'):
        response = client.post('/api/v1/addresses/parse-batch',
                               json=[{'id': 's1', 'raw_address': 'synthetic payload marker'}])
    assert response.status_code == 500
    assert response.json() == {'detail': {'code': 'internal_error'}}
    assert 'X-Jana-Invalid-Items' not in response.headers
    assert client.get('/health').json()['error_count'] == health_before + 1
    events = [r for r in caplog.records if r.message == 'parse_system_failure']
    assert len(events) == 1 and events[0].error_type == 'RuntimeError'
    for record in caplog.records:
        assert 'synthetic payload marker' not in str(record.__dict__)
        assert 'synthetic exception secret' not in str(record.__dict__)
