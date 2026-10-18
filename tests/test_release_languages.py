"""LANG-REGRESSION: public/synthetic EN, KZ and mixed Kazakhstan addresses.

No organizer/holdout fixtures, geo aliases, translation or locale contract.
Each fixture is checked independently through single, batch and in-process ASGI.
"""
import math

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.parser import parse_address, parse_batch

KEYS = ('id', 'raw_address', 'province', 'city', 'district', 'living_area',
        'street_type', 'street_name', 'house', 'apartment', 'entrance', 'floor',
        'landmark', 'confidence')
BASE = '/api/v1/addresses/parse-batch'


def case(label, raw, **expected):
    return pytest.param(raw, expected, id=label)


CASES = [
    case('EN-baseline-street',
         'Almaty, Abay street 12A, apartment 5, entrance 2, floor 3',
         province='Алматы', city='Алматы', street_type='улица', street_name='Abay',
         house='12A', apartment='5', entrance='2', floor='3'),
    case('EN-baseline-avenue',
         'Astana, avenue Respubliki 33/1, apt 7, entrance 2, floor 4',
         province='Астана', city='Астана', street_type='проспект', street_name='Respubliki',
         house='33/1', apartment='7', entrance='2', floor='4'),
    case('EN-baseline-house',
         'Almaty, Zhandosov street, house 55/6, apartment 12',
         province='Алматы', city='Алматы', street_type='улица', street_name='Zhandosov',
         house='55/6', apartment='12'),
    case('KZ-baseline-street',
         'Алматы қаласы, Абай көшесі, 12А үй, 5 пәтер, 2 кіреберіс, 3 қабат',
         province='Алматы', city='Алматы', street_type='улица', street_name='Абай',
         house='12А', apartment='5', entrance='2', floor='3'),
    case('KZ-baseline-avenue',
         'Астана қаласы, Республика даңғылы 33/1, 7 пәтер, 2 кіреберіс, 4 қабат',
         province='Астана', city='Астана', street_type='проспект', street_name='Республика',
         house='33/1', apartment='7', entrance='2', floor='4'),
    case('KZ-baseline-karaganda',
         'Қарағанды, Әлихан Бөкейхан көшесі, 12/А үй, пәтер 05',
         province='Карагандинская область', city='Караганда', street_type='улица',
         street_name='Әлихан Бөкейхан', house='12/А', apartment='05'),
    case('KZ-baseline-details',
         'Шымкент, Төле би көшесі 55/6 үй, 12 пәтер, 3 кіреберіс, 2 қабат',
         province='Шымкент', city='Шымкент', street_type='улица', street_name='Төле би',
         house='55/6', apartment='12', entrance='3', floor='2'),
    case('EN-district-suffix', 'Almaty, Auezov district, Abay street 12A, apt 5',
         province='Алматы', city='Алматы', district='Auezov', street_type='улица',
         street_name='Abay', house='12A', apartment='5'),
    case('KZ-district-suffix', 'Алматы, Әуезов ауданы, Абай көшесі 12Ә, 5 пәтер',
         province='Алматы', city='Алматы', district='Әуезов', street_type='улица',
         street_name='Абай', house='12Ә', apartment='5'),
    case('KZ-micro-prefix-yksham', 'Алматы, ықшамаудан Самал 1, үй 12, пәтер 05',
         province='Алматы', city='Алматы', district='Бостандыкский', street_type='микрорайон',
         street_name='Самал 1', house='12', apartment='05'),
    case('KZ-micro-prefix-shaghyn', 'Алматы, шағынаудан Самал 1, үй 12, пәтер 05',
         province='Алматы', city='Алматы', district='Бостандыкский', street_type='микрорайон',
         street_name='Самал 1', house='12', apartment='05'),
    case('KZ-micro-suffix-yksham', 'Алматы, Самал 1 ықшамаудан, 12 үй, 05 пәтер',
         province='Алматы', city='Алматы', district='Бостандыкский', street_type='микрорайон',
         street_name='Самал 1', house='12', apartment='05'),
    case('KZ-micro-suffix-shaghyn', 'Алматы, Самал 1 шағынаудан, 12 үй, 05 пәтер',
         province='Алматы', city='Алматы', district='Бостандыкский', street_type='микрорайон',
         street_name='Самал 1', house='12', apartment='05'),
    case('EN-karaganda', 'Karaganda, street Abay house 12A, apt 05',
         province='Карагандинская область', city='Караганда', street_type='улица',
         street_name='Abay', house='12A', apartment='05'),
    case('EN-whitespace-newlines', '  Almaty,\nAbay  street  12A\n apartment 05, entrance2, floor3  ',
         province='Алматы', city='Алматы', street_type='улица', street_name='Abay',
         house='12A', apartment='05', entrance='2', floor='3'),
    case('KZ-whitespace-newlines', 'Алматы қаласы,\nАбай көшесі\nүй 12Ә, пәтер05, кіреберіс2, қабат3',
         province='Алматы', city='Алматы', street_type='улица', street_name='Абай',
         house='12Ә', apartment='05', entrance='2', floor='3'),
    case('MIXED-RU-KZ-Latin', 'Алматы/Almaty, Әлихан-Bokeikhan street, дом 12/А, пәтер05, entrance2, этаж3',
         province='Алматы', city='Алматы', street_type='улица', street_name='Әлихан-Bokeikhan',
         house='12/А', apartment='05', entrance='2', floor='3'),
    case('EN-unknown-district', 'Synthetic district, Zhanar street 55/6, apt 05',
         district='Synthetic', street_type='улица', street_name='Zhanar', house='55/6', apartment='05'),
    case('KZ-unknown-district', 'Белгісіз ауданы, Жанар көшесі 12Ә, 05 пәтер',
         district='Белгісіз', street_type='улица', street_name='Жанар', house='12Ә', apartment='05'),
    case('EN-weak-street-no-city', 'Abay street 12A, apt 5',
         street_type='улица', street_name='Abay', house='12A', apartment='5'),
    case('KZ-weak-street-no-city', 'Абай көшесі 12Ә, 5 пәтер',
         street_type='улица', street_name='Абай', house='12Ә', apartment='5'),
    case('EN-no-house-from-fraction', 'Zhanar street 55/6',
         street_type='улица', street_name='Zhanar', house='55/6'),
    case('EN-qualifiers-only', 'apartment 05, entrance 2, floor 3',
         apartment='05', entrance='2', floor='3'),
    case('KZ-qualifiers-only', '05 пәтер, 2 кіреберіс, 3 қабат',
         apartment='05', entrance='2', floor='3'),
    case('EN-conflicting-house', 'Zhanar street, house 12, house 13, apt 05',
         street_type='улица', street_name='Zhanar', apartment='05'),
    case('KZ-conflicting-house', 'Жанар көшесі, 12 үй, 13 үй, 05 пәтер',
         street_type='улица', street_name='Жанар', apartment='05'),
    case('EN-marker-boundary', 'Districtson street 12A',
         street_type='улица', street_name='Districtson', house='12A'),
    case('KZ-marker-boundary', 'Ауданыбек көшесі 12Ә',
         street_type='улица', street_name='Ауданыбек', house='12Ә'),
    case('UNKNOWN-geography', '??? ~~ ^^'),
]


HOUSE_FIRST_CASES = [
    case('EN-house-first-exact', '12 Abay Street, Almaty, apt 5',
         province='Алматы', city='Алматы', street_type='улица', street_name='Abay', house='12', apartment='5'),
    case('EN-house-first-letter', '12A Abay Street, Almaty, apt 05',
         province='Алматы', city='Алматы', street_type='улица', street_name='Abay', house='12A', apartment='05'),
    case('EN-house-first-slash', '33/1 Abay Avenue, Almaty, apt 5',
         province='Алматы', city='Алматы', street_type='проспект', street_name='Abay', house='33/1', apartment='5'),
    case('EN-house-first-slash-letter', '12 / A Abay Street, Almaty, apt 5',
         province='Алматы', city='Алматы', street_type='улица', street_name='Abay', house='12/A', apartment='5'),
    case('EN-suffix-comma-city', 'Abay Street, Almaty, house 12, apt 5',
         province='Алматы', city='Алматы', street_type='улица', street_name='Abay', house='12', apartment='5'),
    case('EN-house-first-no-city', '12 Abay Street, apt 5',
         street_type='улица', street_name='Abay', house='12', apartment='5'),
    case('EN-suffix-no-city', 'Abay Street, apt 5',
         street_type='улица', street_name='Abay', apartment='5'),
    case('EN-real-city-prefix', 'Almaty street Mira12',
         province='Алматы', city='Алматы', street_type='улица', street_name='Mira', house='12'),
    case('EN-numeric-street-explicit-house', '8 March Street, Almaty, house 12',
         province='Алматы', city='Алматы', street_type='улица', street_name='8 March', house='12'),
    case('EN-numeric-street-trailing-house', '8 March Street 12, Almaty',
         province='Алматы', city='Алматы', street_type='улица', street_name='8 March', house='12'),
    case('EN-numeric-only-street', '12 Street, Almaty, house 5',
         province='Алматы', city='Алматы', street_type='улица', street_name='12', house='5'),
    case('EN-micro-not-house-first', '15 microdistrict, house 12',
         street_type='микрорайон', street_name='15', house='12'),
    case('EN-explicit-house-conflict', '12 Abay Street, Almaty, house 13, house 14',
         province='Алматы', city='Алматы', street_type='улица', street_name='12 Abay'),
]


@pytest.mark.parametrize('route', ['single', 'batch', 'asgi'])
@pytest.mark.parametrize(('raw', 'expected'), HOUSE_FIRST_CASES)
def test_house_first_components(route, raw, expected):
    test_language_components(route, raw, expected)


@pytest.mark.parametrize('route', ['single', 'batch', 'asgi'])
@pytest.mark.parametrize(('raw', 'expected'), CASES)
def test_language_components(route, raw, expected):
    row = {'id': 'synthetic', 'raw_address': raw}
    if route == 'single':
        result = parse_address(row)
    elif route == 'batch':
        output = parse_batch([row, row])
        assert len(output) == 2 and output[0] == output[1]
        result = output[0]
    else:
        with TestClient(app) as client:
            response = client.post(BASE, json=[row, row])
        assert response.status_code == 200
        output = response.json()
        assert len(output) == 2 and output[0] == output[1]
        result = output[0]
    assert tuple(result) == KEYS
    assert result['id'] == row['id'] and result['raw_address'] == raw
    assert {key: result[key] for key in KEYS[2:-1]} == {
        key: expected.get(key) for key in KEYS[2:-1]
    }
    assert type(result['confidence']) is float
    assert math.isfinite(result['confidence']) and 0 <= result['confidence'] <= 1
    if result['street_name']:
        assert result['street_name'] in raw


@pytest.mark.parametrize('route', ['batch', 'asgi'])
def test_languages_batch_order_and_duplicate_ids(route):
    rows = [{'id': i % 2, 'raw_address': entry.values[0]}
            for i, entry in enumerate(CASES)]
    rows += [rows[0], rows[-1]]
    expected = [parse_address(row) for row in rows]
    if route == 'batch':
        actual = parse_batch(rows)
    else:
        with TestClient(app) as client:
            response = client.post(BASE, json=rows)
        assert response.status_code == 200
        actual = response.json()
    assert len(actual) == len(rows) and actual == expected


@pytest.mark.parametrize('marker', ['district', 'ауданы'])
@pytest.mark.parametrize('route', ['single', 'batch', 'asgi'])
def test_ambiguous_synthetic_district_does_not_infer_city(monkeypatch, marker, route):
    # Two synthetic owners: extraction must not invent a unique city/region.
    from app import geo
    monkeypatch.setattr(geo, 'get_data', lambda: {
        'cities': [
            {'name': 'SyntheticOne', 'province': 'RegionOne', 'aliases': ['SyntheticOne']},
            {'name': 'SyntheticTwo', 'province': 'RegionTwo', 'aliases': ['SyntheticTwo']},
        ],
        'districts': [
            {'name': 'Shared', 'city': 'SyntheticOne'},
            {'name': 'Shared', 'city': 'SyntheticTwo'},
        ],
    })
    raw = f'Shared {marker}, Zhanar street 12A'
    row = {'id': 'ambiguous', 'raw_address': raw}
    if route == 'single':
        result = parse_address(row)
    elif route == 'batch':
        result = parse_batch([row])[0]
    else:
        with TestClient(app) as client:
            response = client.post(BASE, json=[row])
        assert response.status_code == 200
        result = response.json()[0]
    assert result['district'] == 'Shared'
    assert result['city'] is None and result['province'] is None
    assert result['street_name'] == 'Zhanar' and result['house'] == '12A'
    assert result['raw_address'] == raw and tuple(result) == KEYS
