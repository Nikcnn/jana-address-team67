"""geo-v2 experiment: standalone жилой массив names, Latin/abbreviated provinces, Kazakh 'шағын аудан'.

Synthetic, team-authored rows from public place names only. Never a lookup by full raw string:
every case is checked through general resolver behaviour (positive + negative variants).
"""
import json
from pathlib import Path

import pytest

from app.geo import find_province, find_standalone_area, get_data
from app.parser import parse_address

KEYS = ['id', 'raw_address', 'province', 'city', 'district', 'living_area', 'street_type',
        'street_name', 'house', 'apartment', 'entrance', 'floor', 'landmark', 'confidence']


def p(raw):
    out = parse_address({'id': 'x', 'raw_address': raw})
    assert list(out) == KEYS and out['raw_address'] == raw
    return out


def test_target_prishakhtinsk_with_province_only():
    out = p('Карагандинская область, Пришахтинск, 21 дом')
    assert (out['province'], out['city'], out['district'], out['living_area']) == (
        'Карагандинская область', 'Караганда', 'Алихана Бокейханова', 'Пришахтинск')
    assert out['street_name'] is None and out['street_type'] is None and out['house'] == '21'


@pytest.mark.parametrize('raw,area', [
    ('Караганда, Майкудук, дом 4', 'Майкудук'),
    ('г. Караганда, Сортировка, д. 7, кв 2', 'Сортировка'),
    ('Қарағанды облысы, Майқұдық, 3 үй', 'Майкудук'),
    ('Karaganda, Prishakhtinsk, house 21, apt 4', 'Пришахтинск'),
    ('Karaganda region, Maikuduk, house 9', 'Майкудук'),
    ('Qaragandy oblysy, Sortirovka, house 1', 'Сортировка'),
    ('Қарағанды қаласы, Сұрыптау, 8 үй', 'Сортировка'),
    ('Караганда, ж/м Пришахтинск, дом 2', 'Пришахтинск'),
])
def test_standalone_area_ru_kk_en(raw, area):
    out = p(raw)
    assert out['living_area'] == area and out['city'] == 'Караганда'
    assert out['district'] == 'Алихана Бокейханова' and out['street_name'] is None


def test_area_and_separate_street_both_kept():
    out = p('Караганда, Пришахтинск, ул. Ленина 5')
    assert out['living_area'] == 'Пришахтинск'
    assert (out['street_type'], out['street_name'], out['house']) == ('улица', 'Ленина', '5')


def test_area_with_numbered_microdistrict_keeps_house_separate():
    out = p('Караганда, Майкудук, 12 мкр, дом 5')
    assert out['living_area'] == 'Майкудук'
    assert (out['street_type'], out['street_name'], out['house']) == ('микрорайон', '12', '5')
    kk = p('Қарағанды қаласы, Майқұдық, 12 шағын аудан, 5 үй')
    assert (kk['living_area'], kk['street_type'], kk['street_name'], kk['house'], kk['district']) == (
        'Майкудук', 'микрорайон', '12', '5', 'Алихана Бокейханова')


@pytest.mark.parametrize('raw', ['ул. Пришахтинская 21', 'Караганда, улица Майкудукская 4',
                                 'Қарағанды қаласы, Пришахтинск көшесі 10', 'Караганда, ул. Пришахтинск 3'])
def test_street_named_like_area_is_not_area(raw):
    out = p(raw)
    assert out['living_area'] is None and out['street_name'] is not None


@pytest.mark.parametrize('raw,city', [('г. Темиртау, Пришахтинск, 21 дом', 'Темиртау'),
                                      ('Алматы, Сортировка, дом 3', 'Алматы'),
                                      ('Astana, Maikuduk, house 2', 'Астана')])
def test_explicit_other_city_is_never_replaced(raw, city):
    out = p(raw)
    assert out['city'] == city and out['living_area'] is None and out['district'] is None


def test_conflicting_explicit_province_blocks_inference():
    out = p('Павлодарская область, Майкудук, дом 2')
    assert out['province'] == 'Павлодарская область' and out['city'] is None and out['living_area'] is None


def test_area_followed_by_number_is_ambiguous_and_untouched():
    # 'Пришахтинск 21' may be a microdistrict number: no living_area, no house invention from the mkr.
    out = p('Караганда, мкр Пришахтинск 21, кв 4')
    assert out['living_area'] is None and out['house'] is None and out['apartment'] == '4'


def test_original_spelling_preserved():
    raw = 'Караганда, ПРИШАХТИНСК, ул. ЛЕНИНА 5'
    out = p(raw)
    assert out['raw_address'] == raw and out['street_name'] == 'ЛЕНИНА' and out['living_area'] == 'Пришахтинск'


def test_find_standalone_area_bounded_to_flagged_entries():
    flagged = [a for a in get_data()['living_areas'] if a.get('standalone')]
    assert {a['name'] for a in flagged} == {'Пришахтинск', 'Майкудук', 'Сортировка'}
    for a in flagged:
        assert a['source'] in get_data()['sources'] and 'gov.kz' in a['provenance']
    assert find_standalone_area('Самал 2, дом 5', 'Алматы', None) == (None, None)
    assert find_standalone_area('Пришахтинская', None, None) == (None, None)


@pytest.mark.parametrize('raw,province', [
    ('Karaganda region, Karaganda, Bukhar Zhyrau avenue 47', 'Карагандинская область'),
    ('Qaragandy oblysy, Temirtau, Respublika avenue 20', 'Карагандинская область'),
    ('Pavlodar region, Ekibastuz, Lenin street 10', 'Павлодарская область'),
    ('East Kazakhstan region, Ust-Kamenogorsk, Kazakhstan street 5', 'Восточно-Казахстанская область'),
    ('Kostanay oblast, Kostanay, Baitursynov st 65', 'Костанайская область'),
    ('Актюбинская обл., г. Актобе, пр. Абилкайыр хана 12', 'Актюбинская область'),
    ('Қарағанды обл., Қарағанды қаласы, Бұқар жырау даңғылы 47', 'Карагандинская область'),
])
def test_latin_and_abbreviated_provinces(raw, province):
    out = p(raw)
    assert out['province'] == province
    assert out['street_name'] not in ('region', 'oblast', 'oblysy', 'обл')


def test_latin_province_does_not_eat_city_or_street():
    assert find_province('Almaty, Region street 5') == (None, None)
    out = p('Almaty region, Talgar, Lenin street 5')
    assert (out['province'], out['city'], out['street_name']) == ('Алматинская область', 'Талгар', 'Lenin')
    out = p('Almaty, Abay avenue 150, apt 12')
    assert (out['province'], out['city']) == ('Алматы', 'Алматы')


def test_kazakh_shagyn_audan_is_microdistrict_not_district():
    out = p('Астана қаласы, 5 шағын аудан, 12 үй')
    assert out['district'] is None and (out['street_type'], out['street_name'], out['house']) == ('микрорайон', '5', '12')


def test_extra_province_aliases_are_unique_and_marked():
    owners = {}
    for entry in get_data()['province_aliases']:
        for alias in entry.get('extra_aliases', []):
            key = alias.casefold()
            assert key not in owners, (alias, owners.get(key), entry['name'])
            owners[key] = entry['name']
            assert any(w in key for w in (' обл', 'region', 'oblast', 'oblysy', 'province'))
        if entry.get('extra_aliases'):
            assert entry['extra_aliases_source'] in get_data()['sources']


@pytest.mark.parametrize('raw', ['Караганда, магазин Сортировка', 'ТРЦ Майкудук, Караганда', 'Караганда, возле Пришахтинск, дом 2'])
def test_area_name_after_landmark_marker_stays_landmark(raw):
    out = p(raw)
    assert out['living_area'] is None and out['district'] is None and out['landmark'] is not None


def test_explicit_other_district_blocks_area():
    out = p('Қарағанды қаласы, Қазыбек би ауданы, Майкудук, дом 3')
    assert out['living_area'] is None and out['district'] == 'Қазыбек би'
    ok = p('Қарағанды қаласы, Әлихан Бөкейхан ауданы, Майқұдық, 3 үй')
    assert ok['living_area'] == 'Майкудук'


@pytest.mark.parametrize('raw', ['Сортировка', 'Майкудук, дом 5'])
def test_lone_area_token_without_geo_context_infers_nothing(raw):
    out = p(raw)
    assert out['living_area'] is None and out['city'] is None and out['district'] is None



@pytest.mark.parametrize('raw', ['Karaganda, TRC Prishakhtinsk', 'Karaganda, mall Maikuduk', 'Karaganda, shop Sortirovka',
                                 'Karaganda, Shop. Sortirovka, house 3', 'Karaganda, TC «Maikuduk»'])
def test_latin_landmark_marker_blocks_area(raw):
    out = p(raw)
    assert out['living_area'] is None and out['district'] is None


def test_latin_landmark_markers_need_word_boundary():
    assert p('Karaganda, Prishakhtinsk, house 21')['living_area'] == 'Пришахтинск'
    assert p('Karaganda, Shopping, Maikuduk, house 2')['living_area'] == 'Майкудук'


@pytest.mark.parametrize('raw', ['Karaganda, Maikuduk region street 5', 'Karaganda, Maikuduk Lenin st 5',
                                 'Karaganda, Sortirovka central avenue 7', 'Karaganda, Prishakhtinsk ring road 3',
                                 'Караганда, Майкудук ул. Ленина 5'])
def test_street_context_in_same_segment_wins(raw):
    out = p(raw)
    assert out['living_area'] is None and out['district'] is None
    assert out['street_name'] not in ('region', 'central', 'ring')
