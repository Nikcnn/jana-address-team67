"""Rule-based Kazakhstan address parser, offline and training-free.

Returns the original street spelling; dictionary is used only to enrich inferred fields.
Never caches results by raw_address/id. Confidence is a heuristic, NOT calibrated probability.
"""
from __future__ import annotations
import re
import unicodedata
from typing import Any
from app.geo import find_province, find_city, resolve_area, city_for_district, get_data, boundary_pattern, find_standalone_area

FIELDS=("province","city","district","living_area","street_type","street_name","house","apartment","entrance","floor","landmark")
STREET_TYPES={"ул":"улица","улица":"улица","көшесі":"улица","көше":"улица","пр":"проспект","пр-кт":"проспект","проспект":"проспект","пр-т":"проспект","пер":"переулок","переулок":"переулок","б-р":"бульвар","бульвар":"бульвар","шоссе":"шоссе","проезд":"проезд","наб":"набережная","набережная":"набережная","квартал":"квартал","мкр":"микрорайон","мкрн":"микрорайон","микрорайон":"микрорайон","жк":"жилой комплекс","жилой комплекс":"жилой комплекс","жилой массив":"жилой массив"}
LETTER=r"[A-Za-zА-Яа-яӘәҚқҒғҢңӨөҰұҮүІі]"
# A slash may introduce a number or a single letter; preserve its source case.
TOKEN=rf"[0-9]++(?:\s*+/\s*+(?:[0-9]++{LETTER}?|{LETTER})|{LETTER})?"
# A hyphen belongs to its word, never to a backtracked alternative split.
# Possessive runs require Python >=3.11 (the project targets >=3.12).
WORD=r"[A-Za-zА-Яа-яЁёӘәҚқҒғҢңӨөҰұҮүІіҺһ][A-Za-zА-Яа-яЁёӘәҚқҒғҢңӨөҰұҮүІіҺһ\-]*+"
# Do not take a number from the next hyphenated qualifier ('үй 2-пәтер').
# Prefer marker-number over number-marker: '22 дом 5' labels house 5.
NUMBER_END=r"(?!\w|[-–]\s*[^\W\d_])"


def number_pattern(markers: str) -> re.Pattern:
    marker=rf"(?:{markers})(?![^\W\d_])\.?"
    following=rf"\s*+[:№#.-]?\s*+{TOKEN}{NUMBER_END}"
    return re.compile(
        rf"(?<!\w){marker}\s*+[:№#.-]?\s*+(?P<num>{TOKEN}){NUMBER_END}"
        rf"|(?<!\w)(?P<num2>{TOKEN})\s*+[-–]?\s*+{marker}(?!{following})", re.I)


APT=number_pattern(r"квартира|квартиры|кв|пәтер|flat|apt|apartment|kv")
ENT=number_pattern(r"под[ъь]?езд|под|кіреберіс|entrance")
FLOOR=number_pattern(r"этаж|эт|қабат|floor")
HOUSE=number_pattern(r"дом|д|үй|house")
# Corpus/office are outside the schema: consume without exporting their numbers.
IGNORED=number_pattern(r"корпус|корп|к|офис|оф|office")
STREET_TYPES.update({
    'street':'улица','str':'улица','st':'улица',
    'avenue':'проспект','ave':'проспект','avn':'проспект',
    'boulevard':'бульвар','blvd':'бульвар','lane':'переулок','ln':'переулок',
    'road':'дорога','rd':'дорога','microdistrict':'микрорайон','mkr':'микрорайон',
    'даңғыл':'проспект','даңғылы':'проспект',
    'ықшамаудан':'микрорайон','шағынаудан':'микрорайон',
    'ықшам аудан':'микрорайон','шағын аудан':'микрорайон','шағын ауданы':'микрорайон',
})
TYPE_ALTERNATIVES='|'.join(re.escape(t).replace(r'\ ', r'\s+') for t in sorted(STREET_TYPES,key=len,reverse=True))
PREFIX_TYPES=re.compile(rf"(?<!\w)(?P<type>{TYPE_ALTERNATIVES})(?!\w)\s*\.?\s*",re.I)
SUFFIX_TYPES=re.compile(rf"(?<![\w-])(?P<name>(?:{WORD}|{TOKEN})(?:\s++{WORD}|\s++\d++[а-яa-z]?){{0,3}})\s++(?P<type>{TYPE_ALTERNATIVES})(?!\w)\.?",re.I)
CITY_STREET_BEFORE=re.compile(rf"(?<!\w)(?:{TYPE_ALTERNATIVES})\.?\s*+$",re.I)
CITY_STREET_AFTER=re.compile(rf"^\s*+\.?\s*+(?:{TYPE_ALTERNATIVES})(?!\w)\.?[ \t]*+(?=[,;\n]|\d|$|(?:дом|д|үй|house)(?!\w))",re.I)
EN_HOUSE_FIRST=re.compile(rf"(?P<house>{TOKEN})[ \t]++(?P<name>{WORD}(?:[ \t]++{WORD}){{0,2}})",re.I)
# 'шағын/ықшам аудан' is a microdistrict marker, not a district name.
DIST1=re.compile(rf"(?<![\w-])(?!(?:шағын|ықшам)\s)(?P<name>{WORD}(?:\s++(?!(?:шағын|ықшам)\s){WORD})?)\s++(?:район|р-н|district|ауданы|аудан)\b",re.I)
DIST2=re.compile(rf"(?<!\w)(?:район|р-н)\s++(?P<name>{WORD}(?:\s++{WORD})?)(?!\w)",re.I)
LANDMARK=re.compile(r"(?<!\w)(?:магазин|маркет|аптека|Qazpost|казпочта|трц|тц|школа|возле|рядом\s+с|напротив|ориентир|TRC|TC|mall|shop)\b",re.I)
STOP_LANDMARK=re.compile(r"(?<!\w)(?:\d++\s*+(?:под[ъь]?езд|этаж|эт\.|кв(?:\.|артира)?)|(?:под[ъь]?езд|этаж|кв(?:\.|артира)?|дом|д\.)\s*+\d+|(?:ул\.|улица|проспект|мкр)\s+)",re.I)
HNUM=re.compile(r"(?<![\w])" + TOKEN + r"(?!\w)",re.I)


def blank(record: dict[str,Any]):
    return {"id": record.get("id"),"raw_address":record.get("raw_address"), **{k:None for k in FIELDS},"confidence":0.0}

def remove_span(s: str, start:int, end:int)->str:
    # Keep offsets AND a boundary, preventing raw slices across removed details.
    return s[:start]+(";"+" "*(end-start-1) if end>start else "")+s[end:]

def _has_street_context(scan: str, span: tuple[int, int]) -> bool:
    return bool(CITY_STREET_BEFORE.search(scan[:span[0]])
                or CITY_STREET_AFTER.match(scan[span[1]:]))

def find_address_city(work: str):
    # The geo resolver has its own fixed marker list. Guard new street types
    # here too, so 'Abay boulevard' is not consumed as the city Abay.
    scan=work
    while True:
        city,span=find_city(scan)
        if span is None:return city,span
        if not _has_street_context(scan,span):
            return city,span
        # Mask all rejected mentions of this city in one pass, not one full
        # geo scan per repeated street. Keep offsets and real city mentions.
        rejected={span}
        for alias in city.get('aliases',[city['name']]):
            for match in boundary_pattern(alias).finditer(scan):
                if _has_street_context(scan,match.span()):rejected.add(match.span())
        for start,end in sorted(rejected,reverse=True):scan=remove_span(scan,start,end)

def clean_for_scanning(text:str)->str:
    # One source code point remains one scan code point. Newlines still delimit
    # address segments; other controls/format characters act as token boundaries.
    return ''.join(' ' if unicodedata.category(c) in ('Cc','Cf') and c != '\n' else c for c in text)

def clean_component(value:str)->str:
    # Clean only the captured source value, never raw_address or its offsets.
    # Preserve visible letters, combining accents, punctuation, case and spaces.
    return ''.join(c for c in value if unicodedata.category(c) not in ('Cc','Cf'))

def norm_district(s:str)->str:
    return re.sub(r"\s+"," ",s.strip(" ,.;:-"))

def extract_number(work:str,pattern:re.Pattern):
    found=list(pattern.finditer(work))
    if not found:return None,work,False
    vals=[re.sub(r"\s*/\s*","/",(m.group('num') or m.group('num2')).strip()) for m in found]
    val=vals[0] if len(set(vals))==1 else None
    for m in reversed(found): work=remove_span(work,*m.span())
    return val,work,len(set(vals))>1

def _trim_name(raw:str,start:int,end:int)->str|None:
    name=clean_component(raw[start:end]).strip(' ,.;:\t\n\r-/№')
    return name or None

def _candidate_span(work: str, start: int, micro: bool):
    # determine textual boundary, stop before explicit house/details, commas or landmark
    tail=work[start:]
    boundaries=[len(tail)]
    for m in re.finditer(r"[,;\n]",tail):boundaries.append(m.start())
    for pat in (LANDMARK, HOUSE, APT, ENT, FLOOR):
        m=pat.search(tail)
        if m:boundaries.append(m.start())
    end=min(boundaries)
    if micro:
        follow=PREFIX_TYPES.search(tail)
        if follow and follow.start()>0:end=min(end,follow.start())
    section=tail[:end]
    end=min(end,len(section.rstrip()))
    if micro:
        # e.g. Аксай 3а / Нурсити 2 all part of microdistrict name
        return start+min(end,100)
    num=HNUM.search(section)
    if num:end=min(end,num.start())
    elif re.search(r"[А-Яа-яa-zA-Z][0-9]+",section):
        glue=re.search(r"[А-Яа-яa-zA-Z](?=\d)",section)
        if glue:end=min(end,glue.end())
    return start+min(end,100)

def parse_address(record:dict[str,Any])->dict[str,Any]:
    result=blank(record)
    raw=record.get('raw_address')
    if not isinstance(raw,str) or not raw.strip():return result
    raw_scan=clean_for_scanning(raw)
    work=raw_scan
    score=0.08
    conflict=False
    province,pspan=find_province(work)
    if province:
        result['province']=province;work=remove_span(work,*pspan);score+=.10
    city,cspan=find_address_city(work)
    if city:
        result['city']=city['name'];result['province']=city['province'] if not province else province
        work=remove_span(work,*cspan);score+=.23
        # remove duplicated bilingual city alias, gated to same city
        for alias in sorted(city.get('aliases',[]),key=len,reverse=True):
            for m in list(re.finditer(r"(?<!\w)"+re.escape(alias)+r"(?!\w)",work,re.I)):
                if m.start()<100: work=remove_span(work,*m.span())
        work=re.sub(r"\bқаласы\b","      ",work,flags=re.I)
        work=re.sub(r"\b(?:город|гор|г)\s*\.?\s*(?=[,;/]|\s|$)",lambda m:' '*len(m.group()),work,flags=re.I)
    for pat in (DIST1,DIST2):
        m=pat.search(work)
        if m:
            name=norm_district(clean_component(raw[m.start('name'):m.end('name')]));result['district']=name
            work=remove_span(work,*m.span());score+=.08
            if not result['city']:
                inference=city_for_district(name)
                if inference:result['city']=inference['name'];result['province']=result['province'] or inference['province'];score+=.06
            break
    # Retry the existing resolver after consuming district context. Explicit
    # city text wins over district inference and must not become a street name.
    # Without a consumed district work is unchanged; repeating the same geo scan
    # only doubles its cost on long unknown names.
    if cspan is None and result['district']:
        city,cspan=find_address_city(work)
        if city:
            result['city']=city['name'];result['province']=province or city['province']
            work=remove_span(work,*cspan);score+=.23
            work=re.sub(r"\bқаласы\b",lambda m:' '*len(m.group()),work,flags=re.I)
            work=re.sub(r"\b(?:город|гор|г)\s*\.?\s*(?=[,;/]|\s|$)",lambda m:' '*len(m.group()),work,flags=re.I)
    # geo-v2: a known жилой массив named on its own ('Караганда, Пришахтинск, 21 дом').
    # Dictionary entity match, scoped by explicit city/province; never a raw-address lookup.
    standalone_area=None
    area,aspan=find_standalone_area(work,result['city'],result['province'],result['district'])
    if area:
        standalone_area=area
        result['living_area']=area['name'];work=remove_span(work,*aspan);score+=.08
        if not result['city'] and area.get('city'):
            city_record=next((x for x in get_data()['cities'] if x['name']==area['city']),None)
            if city_record:
                result['city']=city_record['name'];result['province']=result['province'] or city_record['province'];score+=.12
        if not result['district'] and area.get('district'):
            result['district']=area['district'];score+=.05
    # Landmark: segment ends at another address detail or comma; keep exact source casing.
    m=LANDMARK.search(work)
    if m:
        remaining=work[m.start():]
        stop=STOP_LANDMARK.search(remaining,m.end()-m.start())
        end=m.start()+stop.start() if stop else len(work)
        comma=re.search(r"[,;\n]",work[m.start():end])
        if comma:end=min(end,m.start()+comma.start())
        captured=_trim_name(raw,m.start(),end)
        if captured:
            result['landmark']=captured;score+=.03;work=remove_span(work,m.start(),end)
    # Explicit qualifiers removed before greedy street resolution
    conflicts=set()
    for field,pat in [('apartment',APT),('entrance',ENT),('floor',FLOOR),('house',HOUSE)]:
        val,work,clash=extract_number(work,pat)
        if val:result[field]=re.sub(r"\s*/\s*","/",val);score+=.07 if field=='house' else .035
        if clash:conflict=True;conflicts.add(field)
    _,work,_=extract_number(work,IGNORED)
    # Numeric names on both sides of a microdistrict marker are ambiguous.
    # Keep only the type, without inventing a numbering convention (P4).
    ambiguous=re.search(r"(?<!\w)\d+\s+(?:мкрн?|микрорайон)\.?\s+\d+(?!\w)",work,re.I)
    if ambiguous:
        result['street_type']='микрорайон';conflict=True
        work=remove_span(work,*ambiguous.span())
    # Marked street/microdistrict.
    sm=None
    for m in PREFIX_TYPES.finditer(work):
        t=STREET_TYPES.get(re.sub(r'\s+',' ',m.group('type').lower()))
        if t:
            name_start=m.end();name_end=_candidate_span(work,name_start,t in ('микрорайон','квартал','жилой комплекс','жилой массив'))
            # '66квартал' is reversed; handle suffix separately
            name=_trim_name(raw,name_start,name_end)
            if name and re.search(r"[\w]",name):
                sm=(m,t,name,name_start,name_end)
                break
    suffix=None
    for m in SUFFIX_TYPES.finditer(work):
        if m.start()>95:continue
        suffix=(m,STREET_TYPES[re.sub(r'\s+',' ',m.group('type').lower())],_trim_name(raw,m.start('name'),m.end('name')))
        break
    if suffix and (not sm or suffix[0].start() < sm[0].start() or not sm[2]):
        m,t,name=suffix
        if name:
            # Only English non-area suffixes without an independent house.
            # A separate house keeps numeric/date street names intact; an
            # explicit house conflict must never be filled from this prefix.
            if (m.group('type').lower() in ('street','str','st','avenue','ave','avn',
                                           'boulevard','blvd','lane','ln','road','rd')
                    and result['house'] is None and 'house' not in conflicts
                    and not HNUM.search(work,m.end())):
                leading=EN_HOUSE_FIRST.fullmatch(work,m.start('name'),m.end('name'))
                if leading:
                    name=_trim_name(raw,leading.start('name'),leading.end('name'))
                    result['house']=re.sub(r"\s*/\s*","/",leading.group('house'));score+=.07
            result['street_type']=t;result['street_name']=name
            work=remove_span(work,*m.span());score+=.19
    elif sm:
        m,t,name,start,end=sm
        # if a typed street coexists with microdistrict, put mkr/complex in living_area
        if t in ('микрорайон','жилой комплекс','жилой массив'):
            secondary=None
            work2=remove_span(work,m.start(),end)
            for n in PREFIX_TYPES.finditer(work2):
                nt=STREET_TYPES.get(re.sub(r'\s+',' ',n.group('type').lower()))
                if nt in ('улица','проспект','переулок','бульвар','дорога','шоссе','проезд','набережная'):
                    nend=_candidate_span(work2,n.end(),False)
                    nname=_trim_name(raw,n.end(),nend)
                    if nname:secondary=(nt,nname,n.start(),nend);break
            if secondary:
                result['living_area']=name
                result['street_type']=secondary[0];result['street_name']=secondary[1]
                work=remove_span(work,m.start(),end)
                work=remove_span(work,secondary[2],secondary[3]);score+=.22
            else:
                result['street_type']=t;result['street_name']=name
                work=remove_span(work,m.start(),end);score+=.20
        else:
            result['street_type']=t;result['street_name']=name
            work=remove_span(work,m.start(),end);score+=.19
    # Special reversed form: 66квартал / Нурсити 2 микрорайон (even if followed by house)
    if not result['street_name']:
        rev=re.search(rf"(?<!\w)(?P<name>{TOKEN})\s*(?P<kind>квартал)\b",work,re.I)
        if rev:
            result['street_type']='квартал';result['street_name']=_trim_name(raw,rev.start('name'),rev.end('name'))
            work=remove_span(work,*rev.span());score+=.15
    # Do not misinterpret a ЖК as a city; otherwise find unnamed street, e.g. Кулманова 133
    if not result['street_name']:
        # strip residual city prefixes and separators, keeping character indices
        # Do not retry from each inner hyphen of the same maximal word. Together
        # with possessive words/separators this removes combinatorial splitting.
        mm=re.search(rf"(?<![\w-])(?P<name>{WORD}(?:[\s-]++{WORD}){{0,3}})\s*+(?P<house>{TOKEN})(?!\w)",work,re.I)
        if mm:
            name=_trim_name(raw,mm.start('name'),mm.end('name'))
            if name.lower() not in ('дом','д','кв','квартира','подъезд','этаж','район','город','г','микрорайон'):
                result['street_name']=name;score+=.11
                if result['house'] is None and 'house' not in conflicts:
                    result['house']=re.sub(r"\s*/\s*","/",mm.group('house'));score+=.07
                work=remove_span(work,*mm.span())
        elif result['house'] is not None or 'house' in conflicts:
            # With an explicit house, the remaining text before its boundary
            # can still be an untyped street. Never span a removed component.
            mm=re.search(rf"(?<![\w-]){WORD}(?:[ \t]++{WORD}){{0,3}}(?!\w)",work)
            if mm:
                result['street_name']=_trim_name(raw,*mm.span());score+=.11
                work=remove_span(work,*mm.span())
    # If street has already been extracted, the next unlabelled number is house.
    if result['street_name'] and result['house'] is None and 'house' not in conflicts:
        nums=list(HNUM.finditer(work))
        if nums:
            m=nums[0]
            result['house']=re.sub(r"\s*/\s*","/",m.group(0));score+=.055
            work=remove_span(work,*m.span())
    # Geo enrich strictly from local seed and only when uniquely supported.
    a=resolve_area(result['street_name'],result['city']) if result['street_type']=='микрорайон' else None
    if a:
        if not result['city'] and a.get('city'):
            city_record=next((x for x in get_data()['cities'] if x['name']==a['city']),None)
            if city_record:
                result['city']=city_record['name'];result['province']=city_record['province'];score+=.055
        if not result['district'] and a.get('district'):
            result['district']=a['district'];score+=.05
    if result['city'] and not result['province']:
        c=next((x for x in get_data()['cities'] if x['name']==result['city']),None)
        if c:result['province']=c['province']
    if not result['house']:score-=.13
    # A dictionary-confirmed жилой массив is a weaker locator than a street: half credit.
    if not result['street_name']:score-=(.10 if standalone_area else .20)
    if not result['city']:score-=.13
    # Human-readable heuristic, derived from detected fields; not calibrated.
    completeness=(.14 + .24*bool(result['city']) + .24*bool(result['street_name'])
                  + .12*bool(standalone_area and not result['street_name'])
                  + .17*bool(result['house']) + .06*bool(result['street_type'])
                  + .03*bool(result['apartment']) + .025*bool(result['entrance'])
                  + .025*bool(result['floor']) + .04*bool(result['district']))
    score=max(score,completeness)
    if conflict:score-=.17
    result['confidence']=round(max(.02,min(.98,score)),2)
    return result

PARSE_ERROR_COUNT = 0  # system parse failures; counted by the HTTP boundary (app.main), read by /health

def parse_batch(records:list[dict[str,Any]])->list[dict[str,Any]]:
    # Malformed records degrade to one blank record each (C3-v1.1) BEFORE this
    # call; unexpected failures propagate to the caller, which owns counting,
    # logging and the HTTP error response. Never catch-to-fallback here (C7-v1.1).
    outputs:list[dict[str,Any]]=[]
    for record in records:
        if not isinstance(record,dict):
            record={'id':None,'raw_address':None}
        outputs.append(parse_address(record))
    return outputs
