from __future__ import annotations
import asyncio
import json
import time
from fastapi.testclient import TestClient
from app.main import app
client=TestClient(app)
BASE='/api/v1/addresses/parse-batch'
UPLOAD='/api/v1/addresses/upload'
KEYS=['id','raw_address','province','city','district','living_area','street_type','street_name','house','apartment','entrance','floor','landmark','confidence']
def item(identifier='x',raw='Кулманова 133'):return {'id':identifier,'raw_address':raw}
def up(data,filename='test.json'):return client.post(UPLOAD,files={'file':(filename,data,'application/json')})
def test_health_docs_static():
    h=client.get('/health').json();assert h['offline_runtime'] and h['ready'] and h['error_count']==0
    assert set(h['gazetteer'])=={'cities','provinces','living_areas'} and client.get('/docs').status_code==404
    assert client.get('/').status_code==200
def test_envelopes_aliases_serialization():
    original=item(17,'  Кулманова 133\r\n')
    for body,key in [(original,None),([original],None),({'items':[original]},'items'),({'records':[original]},'records'),({'addresses':[original]},'addresses')]:
        r=client.post(BASE,json=body);assert r.status_code==200
        out=r.json() if key is None and isinstance(body,dict) else r.json()[0] if key is None else r.json()[key][0]
        assert list(out)==KEYS and out['id']==17 and type(out['id']) is int and out['raw_address']==original['raw_address']
        assert isinstance(out['confidence'],float) and 0<=out['confidence']<=1
    for route in ('/api/parse-batch','/parse'):assert client.post(route,json=[item()]).status_code==200
def test_order_duplicates_extra_id_invariance():
    entries=[item('dup','первая неизвестная строка'),item('dup','Кулманова 133'),{**item('last'),'ignored':'secret'}]
    first=client.post(BASE,json=entries).json()
    assert [x['id'] for x in first]==['dup','dup','last'] and [x['raw_address'] for x in first]==[x['raw_address'] for x in entries]
    assert 'ignored' not in first[2]
    changed=client.post(BASE,json=[{**entries[1],'id':999}]).json()[0]
    assert changed['id']==999 and {k:v for k,v in changed.items() if k not in ('id','raw_address')}=={k:v for k,v in first[1].items() if k not in ('id','raw_address')}
def test_empty_single_invalid_json():
    assert client.post(BASE,json=[]).json()==[] and client.post(BASE,json={'raw_address':'x','id':'s'}).json()['id']=='s'
    assert client.post(BASE,content=b'{').status_code==400
def null_record(identifier=None,raw=None):
    return {'id':identifier,'raw_address':raw,'province':None,'city':None,'district':None,'living_area':None,'street_type':None,'street_name':None,'house':None,'apartment':None,'entrance':None,'floor':None,'landmark':None,'confidence':0.0}

def test_item_errors_are_degraded_per_record(caplog):
    cases=[None,{'id':'x'},{'raw_address':4,'id':'x'},{'raw_address':'secret'},{'raw_address':'secret','id':True}]
    with caplog.at_level('WARNING'):
        response=client.post(BASE,json=[item('valid-left'),*cases,item('valid-right')])
    assert response.status_code==200 and response.headers['X-Jana-Invalid-Items']=='3'
    outputs=response.json()
    assert len(outputs)==len(cases)+2 and outputs[0]['id']=='valid-left' and outputs[-1]['id']=='valid-right'
    assert outputs[0]['confidence']>0 and outputs[-1]['confidence']>0
    assert outputs[1]==null_record() and outputs[2]==null_record('x') and outputs[3]==null_record('x')
    assert outputs[4]['id'] is None and outputs[4]['raw_address']=='secret' and outputs[4]['confidence']>0
    assert outputs[5]['id'] is None and outputs[5]['raw_address']=='secret' and outputs[5]['confidence']>0
    assert list(outputs[1])==KEYS
    assert 'invalid_items_degraded' in caplog.text and 'count=3' in caplog.text
    for secret in ('secret','valid-left','valid-right'):
        assert secret not in caplog.text and secret not in response.headers.get('X-Jana-Invalid-Items','')

def test_zero_invalid_items_has_no_degraded_header():
    response=client.post(BASE,json=[item('valid','')])
    assert response.status_code==200 and 'X-Jana-Invalid-Items' not in response.headers
    assert response.json()[0]['id']=='valid' and response.json()[0]['raw_address']==''
    assert response.json()[0]['confidence']>=0

def test_mixed_non_object_types_invalid_ids_and_raw_values():
    entries=[item('first'),7,'text',None,[],{'id':None,'raw_address':'Кулманова 133'},{'id':2.5,'raw_address':'Кулманова 133'},{'id':True,'raw_address':'Кулманова 133'},{'id':{},'raw_address':'Кулманова 133'},{'id':'a','raw_address':None},{'id':'b','raw_address':4},{'id':'c','raw_address':{}},item('last')]
    response=client.post(BASE,json=entries)
    assert response.status_code==200 and response.headers['X-Jana-Invalid-Items']=='7'
    outputs=response.json()
    assert len(outputs)==len(entries) and outputs[0]['id']=='first' and outputs[-1]['id']=='last'
    assert all(list(out)==KEYS for out in outputs)
    for index in range(1,5):assert outputs[index]==null_record()
    for index in range(5,9):assert outputs[index]['id'] is None and outputs[index]['raw_address']=='Кулманова 133' and outputs[index]['confidence']>0
    for index in range(9,12):assert outputs[index]==null_record(entries[index]['id'])

def test_id_raw_unicode_length_single_and_duplicates():
    payload=[item('dup','Кулманова 133'),item('dup','Кулманова 133'),item('exact','a'*3000),item('long','b'*3001),item('\ud800','Кулманова 133'),item('badraw','\ud800')]
    response=client.post(BASE,content=json.dumps(payload,ensure_ascii=True).encode(),headers={'content-type':'application/json'})
    assert response.status_code==200 and response.headers['X-Jana-Invalid-Items']=='2'
    out=response.json()
    assert [record['id'] for record in out[:4]]==['dup','dup','exact','long']
    assert out[2]['raw_address']=='a'*3000 and out[2]['confidence']>0
    assert out[3]==null_record('long','b'*3001)
    assert out[4]['id'] is None and out[4]['confidence']>0
    assert out[5]==null_record('badraw')
    single=client.post(BASE,json={'id':'solo','raw_address':None})
    assert single.status_code==200 and single.headers['X-Jana-Invalid-Items']=='1' and single.json()==null_record('solo')
    empty=client.post(BASE,json=[])
    assert empty.status_code==200 and empty.json()==[] and 'X-Jana-Invalid-Items' not in empty.headers

def test_address_limit_both_routes():
    for route in (BASE,UPLOAD):
        bad=client.post(route,json=[item(raw='a'*3001)]) if route==BASE else up(json.dumps([item(raw='a'*3001)]).encode())
        good=client.post(route,json=[item(raw='a'*3000)]) if route==BASE else up(json.dumps([item(raw='a'*3000)]).encode())
        bad_body=bad.json() if route==BASE else bad.json()['items']
        good_body=good.json() if route==BASE else good.json()['items']
        assert bad.status_code==200 and bad_body[0]==null_record('x','a'*3001) and bad.headers['X-Jana-Invalid-Items']=='1'
        assert good.status_code==200 and good_body[0]['raw_address']=='a'*3000 and good_body[0]['confidence']>0
def test_upload_json_jsonl_bom_error_line():
    payload=json.dumps([item('json')],ensure_ascii=False).encode()
    assert up(payload).json()['items'][0]['id']=='json' and up(b'\xef\xbb\xbf'+payload).status_code==200
    lines=(json.dumps(item('a'))+'\n\n'+json.dumps(item('b'))).encode()
    assert [x['id'] for x in up(lines,'batch.jsonl').json()['items']]==['a','b']
    bad=up(b'{"id":"a"}\nnot-json','bad.jsonl')
    assert bad.status_code==400 and bad.json()['detail']['line']==2 and 'not-json' not in bad.text
def test_upload_and_body_caps_items():
    assert up(b' '*(8*1024*1024+1)).status_code==413
    assert client.post(BASE,content=b' '*(32*1024*1024+1),headers={'content-type':'application/json'}).status_code==413
    many=[item(i,'') for i in range(10001)]
    assert client.post(BASE,json=many).status_code==413 and up(json.dumps(many).encode()).status_code==413
def test_restart_determinism():
    from fastapi.testclient import TestClient as FreshClient
    expected=client.post(BASE,json=[item('stable')]).content
    with FreshClient(app) as fresh:assert fresh.post(BASE,json=[item('stable')]).content==expected

def test_lone_surrogates_degrade_without_echo_in_log_or_header(caplog):
    invalid_raw=[(BASE,[item('x','\ud800')]),(UPLOAD,[item('x','\ud800')])]
    for route,bad in invalid_raw:
        response=client.post(route,content=json.dumps(bad).encode(),headers={'content-type':'application/json'}) if route==BASE else up(json.dumps(bad).encode())
        output=response.json() if route==BASE else response.json()['items']
        assert response.status_code==200 and response.headers['X-Jana-Invalid-Items']=='1'
        assert output[0]==null_record('x')
    invalid_id=[(BASE,[item('\ud800','x')]),(UPLOAD,[item('\ud800','x')])]
    for route,bad in invalid_id:
        response=client.post(route,content=json.dumps(bad).encode(),headers={'content-type':'application/json'}) if route==BASE else up(json.dumps(bad).encode())
        output=response.json() if route==BASE else response.json()['items']
        assert response.status_code==200 and 'X-Jana-Invalid-Items' not in response.headers
        assert output[0]['id'] is None and output[0]['raw_address']=='x' and output[0]['confidence']>0

def test_upload_json_jsonl_degrade_invalid_items():
    records=[item('ok-left'),{'id':None,'raw_address':'valid parse'},None,{'id':'large','raw_address':'x'*3001},item('ok-right')]
    json_response=up(json.dumps(records,ensure_ascii=False).encode(),'invalid-record.json')
    assert json_response.status_code==200 and json_response.headers['X-Jana-Invalid-Items']=='2'
    json_output=json_response.json()['items']
    assert len(json_output)==len(records) and json_output[0]['id']=='ok-left' and json_output[-1]['id']=='ok-right'
    assert json_output[1]['id'] is None and json_output[1]['confidence']>0
    assert json_output[2]==null_record() and json_output[3]==null_record('large','x'*3001)
    lines=('\n'.join(json.dumps(record,ensure_ascii=False) for record in records)).encode()
    jsonl_response=up(lines,'invalid-record.jsonl')
    assert jsonl_response.status_code==200 and jsonl_response.headers['X-Jana-Invalid-Items']=='2'
    assert jsonl_response.json()['items']==json_output

def test_deep_json_rejected_as_invalid_json():
    deep=b'['*100000+b']'*100000
    response=client.post(BASE,content=deep,headers={'content-type':'application/json'})
    assert response.status_code==400 and response.json()['detail']['code']=='invalid_json'
    assert up(deep).status_code==400

def test_jsonl_unicode_separators_and_real_line_number():
    records=[item('a','left\u2028middle\u0085right')]
    good=up((json.dumps(records[0],ensure_ascii=False)+'\n').encode('utf-8'),'unicode.jsonl')
    assert good.status_code==200 and good.json()['items'][0]['raw_address']==records[0]['raw_address']
    bad=up((json.dumps(records[0],ensure_ascii=False)+'\nnot-json').encode('utf-8'),'bad.jsonl')
    assert bad.status_code==400 and bad.json()['detail']['line']==2

def test_upload_content_length_rejected_early():
    from starlette.datastructures import UploadFile as StarletteUploadFile
    import io
    from app.main import upload
    async def invoke():
        from starlette.requests import Request as StarletteRequest
        scope={'type':'http','method':'POST','path':UPLOAD,'headers':[(b'content-length',b'8454145')]}
        request=StarletteRequest(scope)
        class UnreadableUploadFile(StarletteUploadFile):
            async def read(self,*args):raise AssertionError('file read before early rejection')
        return await upload(request,UnreadableUploadFile(filename='x.json',file=io.BytesIO(b'[]')))
    import asyncio
    from fastapi import HTTPException
    try:asyncio.run(invoke())
    except HTTPException as exc:assert exc.status_code==413 and exc.detail['code']=='body_too_large'
    else:assert False

def test_upload_file_size_boundary():
    content=b'{"items":[]}'
    exact=up(b' '* (8*1024*1024-len(content))+content)
    assert exact.status_code==200
    over=up(b' '*(8*1024*1024+1-len(content))+content)
    assert over.status_code==413 and over.json()['detail']['code']=='upload_too_large'

def test_chunked_body_cap_and_exact_boundary():
    from starlette.requests import Request
    from app.main import MAX_BODY, read_limited_body

    async def consume(chunks):
        received=0
        async def stream():
            nonlocal received
            for chunk in chunks:
                received+=len(chunk)
                yield chunk
        request=Request({'type':'http','method':'POST','path':BASE,'headers':[]})
        request.stream=stream
        try:
            body=await read_limited_body(request,MAX_BODY)
            return body,received,None
        except Exception as exc:
            return None,received,exc

    chunk=b'x'*(1024*1024)
    oversized=[chunk]*33
    body,received,error=asyncio.run(consume(oversized))
    from fastapi import HTTPException
    assert isinstance(error,HTTPException) and error.status_code==413
    assert error.detail=={'code':'body_too_large'} and received<=MAX_BODY+len(chunk)
    exact_chunks=[chunk]*32
    exact,received,error=asyncio.run(consume(exact_chunks))
    assert error is None and received==MAX_BODY and len(exact)==MAX_BODY

def test_parse_batch_threadpool_preserves_results_and_health_responsive():
    from app.main import parse
    from app.parser import parse_batch
    from starlette.requests import Request
    rows=[item(i,'а-'*1500) for i in range(2000)]
    expected=parse_batch(rows)

    async def exercise():
        raw=json.dumps(rows,ensure_ascii=False).encode()
        messages=[{'type':'http.request','body':raw,'more_body':False}]
        async def receive():return messages.pop(0)
        request=Request({'type':'http','method':'POST','path':BASE,'headers':[(b'content-type',b'application/json'),(b'content-length',str(len(raw)).encode())]},receive)
        batch_task=asyncio.create_task(parse(request))
        await asyncio.sleep(0)
        started=time.perf_counter()
        health_response=await asyncio.to_thread(client.get,'/health')
        health_elapsed=time.perf_counter()-started
        batch_response=await batch_task
        return health_response,health_elapsed,batch_response

    health_response,health_elapsed,batch_response=asyncio.run(exercise())
    assert health_response.status_code==200 and health_elapsed<1.0
    print(f'health_during_batch_seconds={health_elapsed:.6f}')
    actual=[{key:value for key,value in result.items() if key not in ('id','raw_address')} for result in json.loads(batch_response.body)]
    expected_fields=[{key:value for key,value in result.items() if key not in ('id','raw_address')} for result in expected]
    assert actual==expected_fields


def test_deeply_nested_jsonl_line_is_400_not_500():
    """Haiku RC-2 F1: RecursionError в JSONL-ветке не должен превращаться в 500."""
    payload = ("[" * 100000 + "]" * 100000).encode()
    r = up(payload, "deep.jsonl")
    assert r.status_code == 400, (r.status_code, r.text[:200])
    assert r.json()["detail"]["code"] == "invalid_jsonl"
    assert r.json()["detail"]["line"] == 1


def test_deeply_nested_json_body_is_400_not_500():
    payload = ("[" * 100000 + "]" * 100000).encode()
    r = up(payload, "deep.json")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_json"


def test_body_parsing_runs_off_event_loop():
    """Haiku RC-2 F2: json/JSONL разбор тела не должен исполняться в event loop."""
    import ast, pathlib, inspect
    src = pathlib.Path("app/main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    async_handlers = [n for n in ast.walk(tree)
                      if isinstance(n, ast.AsyncFunctionDef) and n.name in ("parse", "upload")]
    assert len(async_handlers) == 2
    for fn in async_handlers:
        assert not [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and n.func.attr in ("loads", "decode")]
    from app import main as main_module
    assert inspect.iscoroutinefunction(main_module.parse) and inspect.iscoroutinefunction(main_module.upload)
    for name in ("_decode_payload", "_decode_upload"):
        assert not inspect.iscoroutinefunction(getattr(main_module, name)), name
