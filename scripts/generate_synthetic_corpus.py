"""Generate synthetic stress corpus. Never pretend it's organizer train.json."""
import json
from pathlib import Path
BASE=Path(__file__).resolve().parent.parent
examples=json.loads((BASE/'data'/'demo_input.json').read_text(encoding='utf-8'))
extra=[
 "Алматы, улица Абая 5 кв 2", "Актобе, проспект Мира 35/1, кв 4",
 "Шымкент, мкр Самал-2, 9 кв 8", "Караганда, ул. Сатпаева 19а, 5 этаж",
 "Атырау, улица Ауэзова 20, 2 подъезд", "Астана, пр-т Туран 17/3, кв 19",
 "Тараз, ул. Панфилова 7, кв 9", "Алматы, жилой комплекс Томирис, ул. Кабанбай батыра 5",
 "Павлодар, ул. Мира 7", "Кызылорда, пр. Абая 3", "пос Боралдай, ул Центральная 12",
 "Ул.   Мира   8   кв  2", "flat 191, kv 12", "", "12/3", "дом 12",
]
inputs=[e['raw_address'] for e in examples]+extra
records=[]
for i in range(1200):
    raw=inputs[i%len(inputs)]
    # Every value is synthetic/illustrative and no labels or model training are included.
    records.append({'id':f'SYN-{i+1:05d}','raw_address':raw})
path=BASE/'data'/'synthetic_corpus_v1.json'
path.write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(f'Generated {len(records)} synthetic input records at {path}')
