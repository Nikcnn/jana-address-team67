"""JANA ADDRESS REST API + offline static UI."""
from __future__ import annotations
import json
import logging
import math
from pathlib import Path
from typing import Any
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from app.geo import get_data
from app.parser import parse_batch
import app.parser as parser_module

ROOT=Path(__file__).resolve().parent
logger=logging.getLogger(__name__)
app=FastAPI(title="JANA ADDRESS | Team 67",description="Offline Kazakhstan address batch parsing (no training data, no external API)",version="0.1.0",docs_url=None,redoc_url=None)
app.mount('/assets',StaticFiles(directory=ROOT/'static'),name='static')
MAX_ITEMS=10000
MAX_UPLOAD=8*1024*1024
MAX_MULTIPART_OVERHEAD=64*1024
MAX_BODY=32*1024*1024
MAX_RAW_ADDRESS=3000
OUTPUT_KEYS=("id","raw_address","province","city","district","living_area","street_type","street_name","house","apartment","entrance","floor","landmark","confidence")
COMPONENT_KEYS=OUTPUT_KEYS[2:-1]  # the 11 address components between raw_address and confidence
SYSTEM_ERROR_CODE='internal_error'

def decode_body(body:Any)->tuple[list[Any],str]:
    if isinstance(body,list):items=body;mode='array'
    elif isinstance(body,dict) and isinstance(body.get('items'),list):items=body['items'];mode='items'
    elif isinstance(body,dict) and isinstance(body.get('records'),list):items=body['records'];mode='records'
    elif isinstance(body,dict) and isinstance(body.get('addresses'),list):items=body['addresses'];mode='addresses'
    elif isinstance(body,dict) and 'raw_address' in body:items=[body];mode='single'
    else:raise HTTPException(status_code=422,detail={'code':'invalid_envelope'})
    if len(items)>MAX_ITEMS:raise HTTPException(status_code=413,detail={'code':'too_many_items'})
    return items,mode

def process_items(items:list[Any])->tuple[list[dict[str,Any]|None],int,list[int],list[dict[str,Any]],list[int]]:
    valid_items=[];valid_indexes=[];results:list[dict[str,Any]|None]=[None]*len(items);invalid_indexes=[]
    for index,item in enumerate(items):
        identifier=item.get('id') if isinstance(item,dict) else None
        if isinstance(identifier,bool) or not isinstance(identifier,(str,int)):
            identifier=None
        if isinstance(identifier,str):
            try:identifier.encode('utf-8')
            except UnicodeEncodeError:identifier=None
        raw_address=item.get('raw_address') if isinstance(item,dict) else None
        raw_valid=isinstance(raw_address,str)
        if raw_valid:
            try:raw_address.encode('utf-8')
            except UnicodeEncodeError:raw_valid=False
        if not raw_valid or len(raw_address)>MAX_RAW_ADDRESS:
            output_raw=raw_address if raw_valid and len(raw_address)>MAX_RAW_ADDRESS else None
            results[index]={key:(identifier if key=='id' else output_raw if key=='raw_address' else 0.0 if key=='confidence' else None) for key in OUTPUT_KEYS}
            invalid_indexes.append(index)
            continue
        valid_items.append({'id':identifier,'raw_address':raw_address})
        valid_indexes.append(index)
    return results, len(invalid_indexes), invalid_indexes[:20], valid_items, valid_indexes

def assemble_results(results:list[dict[str,Any]|None],invalid_count:int,invalid_indexes:list[int],valid_items:list[dict[str,Any]],valid_indexes:list[int],parsed:list[dict[str,Any]])->tuple[list[dict[str,Any]],int,list[int]]:
    if len(parsed)!=len(valid_items):
        raise RuntimeError('parse_batch returned unexpected result count')
    for index,item,result in zip(valid_indexes,valid_items,parsed):
        results[index]=serialize_result(item,result)
    return [result for result in results if result is not None],invalid_count,invalid_indexes

def serialize_result(item:dict[str,Any],parsed:dict[str,Any])->dict[str,Any]:
    return {key:(item['id'] if key=='id' else item['raw_address'] if key=='raw_address' else parsed.get(key)) for key in OUTPUT_KEYS}

def validate_parsed_output(parsed:Any,expected_count:int)->None:
    # The HTTP layer owns the response contract: a malformed parser return must
    # become a system failure, never a silently degraded 200 response.
    if not isinstance(parsed,list) or len(parsed)!=expected_count:
        raise ValueError('parse_batch cardinality mismatch')
    for result in parsed:
        if not isinstance(result,dict) or any(key not in result for key in OUTPUT_KEYS):
            raise ValueError('parse_batch result shape invalid')
        confidence=result['confidence']
        if type(confidence) is not float or not math.isfinite(confidence) or not 0.0<=confidence<=1.0:
            raise ValueError('parse_batch confidence invalid')
        for key in COMPONENT_KEYS:
            value=result[key]
            if value is not None and (not isinstance(value,str) or value==''):
                raise ValueError('parse_batch component invalid')

async def process_batch(items:list[Any])->tuple[list[dict[str,Any]],int,list[int]]:
    results,invalid_count,invalid_indexes,valid_items,valid_indexes=process_items(items)
    parsed=await run_in_threadpool(parse_batch,valid_items)
    validate_parsed_output(parsed,len(valid_items))
    results,invalid_count,invalid_indexes=assemble_results(results,invalid_count,invalid_indexes,valid_items,valid_indexes,parsed)
    return results,invalid_count,invalid_indexes

def _system_failure(exc:BaseException)->HTTPException:
    # Owning boundary for unexpected parse-system failures: count once, log once
    # (metadata only: no payload, id or exception message), keep the cause.
    parser_module.PARSE_ERROR_COUNT=parser_module.PARSE_ERROR_COUNT+1
    logger.error('parse_system_failure',extra={'error_type':type(exc).__name__})
    return HTTPException(status_code=500,detail={'code':SYSTEM_ERROR_CODE})

def encode_response(results:list[dict],mode:str):
    if mode=='array':return results
    if mode=='single':return results[0]
    return {mode:results}

async def read_limited_body(request:Request,limit:int)->bytes:
    content_length=request.headers.get('content-length')
    if content_length:
        try:
            if int(content_length)>limit:raise HTTPException(status_code=413,detail={'code':'body_too_large'})
        except ValueError:raise HTTPException(status_code=400,detail={'code':'invalid_content_length'})
    chunks=[];size=0
    async for chunk in request.stream():
        size+=len(chunk)
        if size>limit:raise HTTPException(status_code=413,detail={'code':'body_too_large'})
        chunks.append(chunk)
    return b''.join(chunks)

@app.get('/')
async def index():return FileResponse(ROOT/'static'/'index.html')

@app.get('/health')
async def health():
    data=get_data();cities=len(data['cities']);provinces=len(data['provinces']);areas=len(data.get('living_areas',[]))
    return {'status':'ok','offline_runtime':True,'cities_in_local_seed':cities,'gazetteer_type':data['_meta']['kind'],'version':'0.1.0','ready':True,'error_count':getattr(parser_module,'PARSE_ERROR_COUNT',0),'gazetteer':{'cities':cities,'provinces':provinces,'living_areas':areas}}

@app.post('/api/v1/addresses/parse-batch')
@app.post('/api/parse-batch')
@app.post('/parse')
async def parse(request:Request):
    try:
        body_bytes=await read_limited_body(request,MAX_BODY)
        body=await run_in_threadpool(_decode_payload,body_bytes)
        items,mode=decode_body(body)
        results,invalid_count,invalid_indexes=await process_batch(items)
    except HTTPException:
        raise
    except Exception as exc:
        raise _system_failure(exc) from exc
    headers={}
    if invalid_count:
        headers['X-Jana-Invalid-Items']=str(invalid_count)
        logger.warning('invalid_items_degraded count=%d indexes=%s',invalid_count,invalid_indexes)
    return JSONResponse(encode_response(results,mode),headers=headers)

def _decode_payload(body_bytes:bytes)->Any:
    try:return json.loads(body_bytes)
    except (ValueError,UnicodeDecodeError,RecursionError):raise HTTPException(status_code=400,detail={'code':'invalid_json'})

def _decode_upload(data:bytes,is_jsonl:bool)->Any:
    try:decoded=data.decode('utf-8-sig')
    except UnicodeDecodeError:raise HTTPException(status_code=400,detail={'code':'invalid_upload_encoding'})
    if is_jsonl:
        rows=[]
        for line_number,line in enumerate(decoded.split('\n'),1):
            line=line.rstrip('\r')
            if not line.strip():continue
            try:rows.append(json.loads(line))
            except (ValueError,RecursionError):raise HTTPException(status_code=400,detail={'code':'invalid_jsonl','line':line_number})
        return rows
    try:return json.loads(decoded)
    except (ValueError,RecursionError):raise HTTPException(status_code=400,detail={'code':'invalid_json'})

@app.post('/api/v1/addresses/upload')
async def upload(request:Request,file:UploadFile=File(...)):
    content_length=request.headers.get('content-length')
    if content_length:
        try:
            if int(content_length)>MAX_UPLOAD+MAX_MULTIPART_OVERHEAD:raise HTTPException(status_code=413,detail={'code':'body_too_large'})
        except ValueError:raise HTTPException(status_code=400,detail={'code':'invalid_content_length'})
    data=await file.read(MAX_UPLOAD+1)
    if len(data)>MAX_UPLOAD:raise HTTPException(status_code=413,detail={'code':'upload_too_large'})
    try:
        is_jsonl=bool(file.filename and file.filename.lower().endswith('.jsonl'))
        body=await run_in_threadpool(_decode_upload,data,is_jsonl)
        items,_mode=decode_body(body)
        results,invalid_count,invalid_indexes=await process_batch(items)
    except HTTPException:
        raise
    except Exception as exc:
        raise _system_failure(exc) from exc
    headers={}
    if invalid_count:
        headers['X-Jana-Invalid-Items']=str(invalid_count)
        logger.warning('invalid_items_degraded count=%d indexes=%s',invalid_count,invalid_indexes)
    return JSONResponse({'items':results},headers=headers)

@app.get('/api/v1/addresses/demo')
async def demo():
    data=json.loads((ROOT.parent/'data'/'demo_input.json').read_text(encoding='utf-8'))
    return {'items':data}
