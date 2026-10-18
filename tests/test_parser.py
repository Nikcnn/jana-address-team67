from app.parser import parse_address,parse_batch,FIELDS
import json
import math
import os
import subprocess
import sys
import unicodedata
from itertools import permutations

import pytest

def p(s):return parse_address({'id':'t','raw_address':s})

def test_official_sample():
    r=p('город Алматы/Алматы қаласы, Мкр Аксай 3а, 59, кв 70')
    assert r['province']=='Алматы' and r['city']=='Алматы'
    assert r['district']=='Ауэзовский'
    assert r['street_type']=='микрорайон' and r['street_name']=='Аксай 3а'
    assert r['house']=='59' and r['apartment']=='70'

def test_kvartal():
    r=p('66квартал 12дом 69квартира')
    assert r['street_type']=='квартал' and r['street_name']=='66'
    assert r['house']=='12' and r['apartment']=='69'

def test_typo():
    r=p('Ул .Курильская 9 кв 1')
    assert r['street_type']=='улица' and r['street_name']=='Курильская'
    assert r['house']=='9' and r['apartment']=='1'

def test_house_fraction_landmark():
    r=p('Туран 55/6 магазин береке 4подьезд 9этаж')
    assert r['street_name']=='Туран' and r['house']=='55/6'
    assert r['entrance']=='4' and r['floor']=='9' and r['landmark']=='магазин береке'

def test_reverse_micro():
    r=p('Нурсити 2 микрорайон 11дом 5 подьезд 239 кв')
    assert r['street_name']=='Нурсити 2' and r['street_type']=='микрорайон'
    assert r['house']=='11' and r['entrance']=='5' and r['apartment']=='239'

def test_bilingual():
    r=p('г. Актобе/Ақтөбе қаласы, проспект Абая 33/1, кв. 12')
    assert r['city']=='Актобе' and r['province']=='Актюбинская область'
    assert r['street_name']=='Абая' and r['house']=='33/1'

def test_live_street_name_as_written():
    r=p('город Шымкент, улица казибек би 12а, 3 подъезд, 5 этаж')
    assert r['city']=='Шымкент' and r['street_name']=='казибек би'
    assert r['house']=='12а' and r['entrance']=='3' and r['floor']=='5'

def test_living_area():
    r=p('жилой массив Пришахтинск улица Мира дом 8')
    assert r['living_area']=='Пришахтинск' and r['street_name']=='Мира' and r['house']=='8'

def test_explicit_district():
    r=p('Ауэзовский район, ул. Жандосова 5')
    assert r['district']=='Ауэзовский' and r['street_name']=='Жандосова' and r['house']=='5'

def test_untyped():
    r=p('Кулманова 133')
    assert r['street_type'] is None and r['street_name']=='Кулманова' and r['house']=='133'

def test_glued():
    r=p('Назарбаева8 кв5')
    assert r['street_name']=='Назарбаева' and r['house']=='8' and r['apartment']=='5'

def test_empty():
    r=p('   ')
    assert all(r[k] is None for k in FIELDS)
    assert r['raw_address']=='   ' and r['confidence']==0

def test_raw_preserved_and_batch_cardinality():
    rows=[{'id':str(i),'raw_address':s} for i,s in enumerate(['\u2060пр. Абая 10','Ул.   Мира   8   кв  2','дом 12',''])]
    out=parse_batch(rows)
    assert len(out)==len(rows)
    assert all(o['id']==v['id'] and o['raw_address']==v['raw_address'] for o,v in zip(out,rows))
    assert all(set(FIELDS).issubset(o) and 0<=o['confidence']<=1 for o in out)

def test_incomplete_invalid():
    res=parse_batch([None,{'id':'bad','raw_address':34},{'id':'good','raw_address':'дом 12'}])
    assert len(res)==3 and res[1]['confidence']==0 and res[2]['house']=='12'


@pytest.mark.parametrize(('raw', 'expected'), [
    pytest.param('Тараз Жамбыла 100/2 кв.4 3-этаж',
                 dict(street_name='Жамбыла', house='100/2', apartment='4', floor='3', entrance=None), id='P1'),
    pytest.param('г Семей ул Мира д 3 кв 4 подъезд 2 этаж 3',
                 dict(street_name='Мира', house='3', apartment='4', entrance='2', floor='3'), id='P2'),
    pytest.param('мкр Самал-2 д 33 кв 12',
                 dict(street_type='микрорайон', street_name='Самал-2', house='33', apartment='12'), id='P3'),
    pytest.param('Актау 15 мкр 22 дом 5 кв 8',
                 dict(city='Актау', house='5', apartment='8'), id='P4'),
    ('66квартал 12дом 69квартира', dict(street_type='квартал', street_name='66', house='12', apartment='69')),
    ('Назарбаева8 кв5', dict(street_name='Назарбаева', house='8', apartment='5')),
    ('4подъезд 9этаж', dict(entrance='4', floor='9', street_name=None, house=None)),
    ('Алатауский район Алматы Утепова 10', dict(district='Алатауский', city='Алматы', street_name='Утепова', house='10')),
    ('Алматы қаласы, Абай даңғылы, 13Б үй, 2-пәтер', dict(city='Алматы', street_type='проспект', street_name='Абай', house='13Б', apartment='2')),
    ('Алматы, Ауэзовский район, Абай көшесі 89/1', dict(district='Ауэзовский', street_type='улица', street_name='Абай', house='89/1', apartment=None)),
    ('Абай көшесі 13Б үй 2-пәтер 4-кіреберіс 9-қабат', dict(street_type='улица', street_name='Абай', house='13Б', apartment='2', entrance='4', floor='9')),
    ('Abay st 5, kv 2', dict(street_name='Abay', street_type='улица', house='5', apartment='2')),
    ('st Abay 5 flat 2', dict(street_name='Abay', house='5', apartment='2')),
    ('улица казибек би 5', dict(street_name='казибек би', house='5')),
    ('Тимирязева 42 к1 кв 9', dict(street_name='Тимирязева', house='42', apartment='9', entrance=None, floor=None)),
    ('Тимирязева дом 42 корпус 1 офис 7 кв 9', dict(street_name='Тимирязева', house='42', apartment='9', entrance=None, floor=None)),
])
def test_segmentation_regressions(raw, expected):
    result=p(raw)
    assert {key: result[key] for key in expected} == expected
    if result['street_name']:
        assert result['street_name'] in raw
        assert not any(marker in result['street_name'].lower()
                       for marker in ('кв', 'подъезд', 'этаж', 'дом', 'пәтер'))
    if raw.startswith('Актау'):
        assert result['street_name'] != '5 кв 8'


@pytest.mark.parametrize(('field', 'markers'), [
    ('house', 'дом 3 дом 5'),
    ('apartment', 'кв 3 кв 5'),
    ('entrance', 'подъезд 3 подъезд 5'),
    ('floor', 'этаж 3 этаж 5'),
])
def test_numeric_conflicts_stay_unknown(field, markers):
    prefix='Алматы улица Мира ' + ('' if field == 'house' else 'дом 10 ')
    result=p(prefix + markers)
    assert result[field] is None
    assert result['street_name'] == 'Мира'
    assert result['confidence'] < p(prefix + markers.split(' ', 2)[0] + ' 3')['confidence']


@pytest.mark.parametrize('details', list(permutations(('дом 10', 'кв 4', 'подъезд 2', 'этаж 3'))))
def test_detail_order_does_not_change_numeric_ownership(details):
    result=p('улица Мира ' + ' '.join(details))
    assert [result[k] for k in ('street_name', 'house', 'apartment', 'entrance', 'floor')] == ['Мира', '10', '4', '2', '3']


@pytest.mark.parametrize('prefix', ['улица Мира ', 'Мира '])
def test_house_conflict_cannot_be_filled_from_residual_number(prefix):
    result=p(prefix + '99 дом 3 дом 5')
    assert result['house'] is None
    assert result['street_name'] == 'Мира'


def test_equal_numeric_mentions_are_not_conflicts():
    raw='улица Мира дом 10 / 2 дом 10/2 кв 4 кв 4'
    result=p(raw)
    assert result['house'] == '10/2' and result['apartment'] == '4'
    assert result['confidence'] == p('улица Мира дом 10/2 кв 4')['confidence']


def test_slash_house_without_apartment_and_ignored_corpus():
    result=p('улица Мира 100/2 к1 офис 7')
    assert result['house'] == '100/2' and result['apartment'] is None
    assert result['street_name'] == 'Мира'
    assert p('улица Мира к1 офис 7 кв 9')['house'] is None


def test_ambiguous_numeric_microdistrict_is_conservative():
    result=p('Актау 15 мкр 22 дом 5 кв 8')
    assert result['street_type'] == 'микрорайон'
    assert result['street_name'] is None and result['living_area'] is None
    assert result['house'] == '5' and result['apartment'] == '8'
    assert result['confidence'] < p('Актау мкр Самал дом 5 кв 8')['confidence']


@pytest.mark.parametrize('raw', [
    '', '   ', '\u200bул. Мира 10\r\nкв 2\ufeff',
    'Алматы\r\nАбай көшесі 89/1', 'улица Ка\u200dзибек би 5',
    'улица ка\u0301зибек би 5', '😀\x00\u2060\t\r\n', 'Ж' * 3000,
    'улица Мира 10 кв 2' + ' ' * 2982,
])
def test_output_contract_and_id_invariance(raw):
    result=parse_address({'id':17, 'raw_address':raw, 'ignored':'extra'})
    assert list(result) == ['id', 'raw_address', *FIELDS, 'confidence']
    assert len(result) == 14
    assert result['raw_address'] == raw
    assert [ord(c) for c in result['raw_address']] == [ord(c) for c in raw]
    assert result['id'] == 17 and type(result['id']) is int
    assert all(result[key] is None or isinstance(result[key], str) and result[key]
               for key in FIELDS)
    assert type(result['confidence']) is float
    assert math.isfinite(result['confidence']) and 0 <= result['confidence'] <= 1
    changed=parse_address({'id':'another', 'raw_address':raw})
    assert {k:v for k,v in result.items() if k != 'id'} == {k:v for k,v in changed.items() if k != 'id'}
    assert parse_address({'id':17, 'raw_address':raw}) == result


def test_batch_determinism_and_restart():
    rows=[{'id':i % 2, 'raw_address':s} for i,s in enumerate([
        'Тараз Жамбыла 100/2 кв.4 3-этаж', 'мкр Самал-2 д 33 кв 12',
        'Алматы қаласы, Абай даңғылы, 13Б үй, 2-пәтер', '', 'Ж' * 3000,
        'улица казибек би 5', 'Тараз Жамбыла 100/2 кв.4 3-этаж',
    ])]
    first=parse_batch(rows)
    assert parse_batch(rows) == first
    script='import json,sys; from app.parser import parse_batch; print(json.dumps(parse_batch(json.load(sys.stdin)),ensure_ascii=False))'
    restarted=subprocess.run([sys.executable, '-c', script], input=json.dumps(rows),
                             text=True, capture_output=True, check=True, timeout=30,
                             env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'})
    assert json.loads(restarted.stdout) == first


PATHOLOGICAL_INPUTS = [
    'а-' * 80, 'а-' * 160, 'а-' * 1500,
    'a-' * 100, 'a-' * 1500, 'аб-' * 1000,
    'г. ' + 'а-' * 60,
    ('г. ' + 'а-' * 1500)[:3000],
    ('Abай-Қа-' * 400)[:3000],
    ('ул. ' + 'а-' * 1500)[:3000],
    ('район ' + 'а-' * 1500)[:3000],
    ('а-' * 1490) + ' көшесі 12',
    ('а-' * 1490) + ' район',
    ('а-' * 1490) + ' 12',
    'А' + '-' * 2999,
    ('Мира ' + '-' * 3000)[:3000],
    ('дом ' + ' ' * 3000)[:3000],
    ('кв ' + ' ' * 2990) + '!',
    ('Мира ' + ' ' * 2990) + '!',
    '1' * 3000, ('1/' * 1500),
    ('дом ' + '1' * 2995) + 'x',
    ('а ' * 1498) + '!',
    ('1 ' * 1498) + '!',
]


def test_pathological_inputs_have_bounded_runtime():
    # A subprocess watchdog stops a regressed regex from hanging the test runner;
    # the parser itself has no timeout wrapper. Imports/startup are outside budget.
    script = '''
import json, sys, time
from app.parser import parse_address
rows = json.load(sys.stdin)
parse_address({'raw_address': 'улица Мира 5'})
started = time.perf_counter()
for raw in rows:
    assert len(raw) <= 3000
    result = parse_address({'id': 'budget', 'raw_address': raw})
    assert result['raw_address'] == raw
elapsed = time.perf_counter() - started
assert elapsed <= 2, f'pathological set took {elapsed:.3f}s (budget 2s)'
print(elapsed)
'''
    run = subprocess.run([sys.executable, '-c', script],
                         input=json.dumps(PATHOLOGICAL_INPUTS), text=True,
                         capture_output=True, timeout=8, check=True,
                         env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert float(run.stdout) <= 2


@pytest.mark.parametrize('invisible', [
    '\u200b', '\u200c', '\u200d', '\u2060', '\ufeff', '\u00ad',
    '\u200e', '\u202e', '\u2066', '\x00', '\x1f', '\x7f', '\x85',
    '\t', '\r',
])
@pytest.mark.parametrize(('template', 'field', 'expected'), [
    ('ул. Ми{}ра 5', 'street_name', 'Мира'),
    ('ул. Абая{} 7', 'street_name', 'Абая'),
    ('Ми{}ра 5', 'street_name', 'Мира'),
    ('Син{}тетический район, ул. Мира 5', 'district', 'Синтетический'),
    ('жилой массив Са{}мал улица Мира 5', 'living_area', 'Самал'),
    ('ул. Мира 5, магазин Бе{}реке', 'landmark', 'магазин Береке'),
])
def test_invisible_characters_do_not_leak_into_components(invisible, template, field, expected):
    raw = template.format(invisible)
    result = p(raw)
    assert result['raw_address'] == raw
    assert result[field] == expected
    for key in FIELDS:
        assert result[key] is None or not any(
            unicodedata.category(char) in ('Cc', 'Cf') for char in result[key])


def test_invisible_separator_keeps_house_and_source_offsets():
    raw = 'Алматы, ул. Ми\u0301РА\u200b5, кв 2'
    result = p(raw)
    assert result['raw_address'] == raw
    assert result['street_name'] == 'Ми\u0301РА'
    assert result['city'] == 'Алматы'
    assert result['house'] == '5' and result['apartment'] == '2'


@pytest.mark.parametrize(('raw', 'expected'), [
    ('Жан-Поль Сартр 12', 'Жан-Поль Сартр'),
    ('Abай-Қадыр 12', 'Abай-Қадыр'),
    ('улица ка\u0301зибек би 5', 'ка\u0301зибек би'),
    ('ул.  МиРа   Әліби-Жангелдин 5', 'МиРа   Әліби-Жангелдин'),
])
def test_visible_street_spelling_is_not_normalized(raw, expected):
    result = p(raw)
    assert result['street_name'] == expected
    assert result['raw_address'] == raw


def test_newline_still_delimits_components():
    raw = 'ул. Мира\nдом 5, магазин Береке\nкв 2'
    result = p(raw)
    assert result['raw_address'] == raw
    assert result['street_name'] == 'Мира'
    assert result['landmark'] == 'магазин Береке'
    assert result['house'] == '5' and result['apartment'] == '2'


@pytest.mark.parametrize(('marker', 'street_type'), [
    ('street', 'улица'), ('str', 'улица'), ('st', 'улица'),
    ('avenue', 'проспект'), ('ave', 'проспект'), ('avn', 'проспект'),
    ('boulevard', 'бульвар'), ('blvd', 'бульвар'),
    ('lane', 'переулок'), ('ln', 'переулок'),
    ('road', 'дорога'), ('rd', 'дорога'),
    ('microdistrict', 'микрорайон'), ('mkr', 'микрорайон'),
])
@pytest.mark.parametrize('suffix', [False, True])
@pytest.mark.parametrize('uppercase_dotted', [False, True])
def test_latin_street_types_in_both_positions(marker, street_type, suffix, uppercase_dotted):
    if uppercase_dotted:
        marker = marker.upper() + '.'
    name = 'Abay'
    street = f'{name} {marker}' if suffix else f'{marker} {name}'
    # A microdistrict can contain numbers in its name; label its house explicitly.
    house = 'house 5' if street_type == 'микрорайон' else '5'
    raw = f'{street} {house}, flat 2'
    result = p(raw)
    assert result['street_type'] == street_type
    assert result['street_name'] == name
    assert result['house'] == '5' and result['apartment'] == '2'
    assert result['raw_address'] == raw
    assert result['city'] is None and result['province'] is None


@pytest.mark.parametrize(('raw', 'name', 'street_type'), [
    ('Abay street 5, flat 2', 'Abay', 'улица'),
    ('street Jean-Paul 5', 'Jean-Paul', 'улица'),
    ('Jean-Paul  Street 5', 'Jean-Paul', 'улица'),
    ('ул. John Street 5', 'John Street', 'улица'),
    ('ул. Street 5', 'Street', 'улица'),
    ('Street 5', 'Street', None),
    ('Streetman 5', 'Streetman', None),
    ('Abaystreet 5', 'Abaystreet', None),
    ('Zhanar streetwise 5', 'Zhanar streetwise', None),
    ('Stratford 5', 'Stratford', None),
    ('Avenueville 5', 'Avenueville', None),
])
def test_latin_type_boundaries_and_surname(raw, name, street_type):
    result = p(raw)
    assert result['street_name'] == name
    assert result['street_type'] == street_type
    assert result['house'] == '5'


@pytest.mark.parametrize('raw', [
    'Almaty boulevard Zhanar 5', 'boulevard Zhanar 5, Almaty',
    'Abay boulevard 5, Almaty', 'Almaty, microdistrict Abay house 5',
])
def test_latin_type_city_guard_keeps_distinct_city_mentions(raw):
    result = p(raw)
    assert result['city'] == 'Алматы' and result['province'] == 'Алматы'
    assert result['street_name'] == ('Zhanar' if 'Zhanar' in raw else 'Abay')
    assert result['house'] == '5'


@pytest.mark.parametrize(('token', 'house'), [
    ('12/А', '12/А'), ('12/а', '12/а'), ('12 / А', '12/А'),
    ('12 / а', '12/а'), ('12/A', '12/A'), ('12/a', '12/a'),
    ('12/Ә', '12/Ә'), ('100/2', '100/2'), ('89/1', '89/1'),
    ('13Б', '13Б'), ('12а', '12а'), ('007', '007'),
    ('12 / 2Б', '12/2Б'),
])
@pytest.mark.parametrize('template', [
    'ул. Абая {}', 'Абая {}', 'ул. Абая дом {}', 'ул. Абая {} дом',
])
@pytest.mark.parametrize('details', ['', ', кв 4'])
def test_house_slash_letter_and_existing_number_forms(token, house, template, details):
    raw = template.format(token) + details
    result = p(raw)
    assert result['street_name'] == 'Абая'
    assert result['house'] == house and type(result['house']) is str
    assert result['apartment'] == ('4' if details else None)
    assert result['entrance'] is None and result['floor'] is None
    assert result['raw_address'] == raw
