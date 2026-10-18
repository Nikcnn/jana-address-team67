import json,time,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.parser import parse_batch
p=Path(__file__).resolve().parent.parent/'data'/'synthetic_corpus_v1.json'
rows=json.loads(p.read_text(encoding='utf-8'))
start=time.perf_counter()
res=parse_batch(rows[:1000]);seconds=time.perf_counter()-start
assert len(res)==1000
assert all(r['raw_address']==i['raw_address'] and r['id']==i['id'] for r,i in zip(res,rows))
print(json.dumps({'records':len(res),'seconds':round(seconds,5),'throughput_per_second':round(len(res)/seconds,1),'type':'synthetic speed test, not accuracy'},ensure_ascii=False))
