# JANA ADDRESS — Team №67

Разбор адресов Казахстана на русском, казахском и английском в 14 полей: batch REST API, веб-интерфейс загрузки / просмотра / ручной правки / экспорта, офлайн-runtime, запуск через Docker Compose. Хакатон Jana Post × Terricon Valley, challenge 01.

Примеры поддержанных форм: `12 Abay Street, Almaty, apt 5`; `Алматы қаласы, Абай көшесі, 12Ә үй, 5 пәтер, 2 кіреберіс, 3 қабат`; `Астана қаласы, Республика даңғылы 33/1, 7 пәтер`. `raw_address` и написание улицы сохраняются; city/province берутся в существующей канонической форме справочника, а не переводятся на язык входа. Языковые регрессии: `tests/test_release_languages.py`. Это ограниченная грамматика, не универсальный международный parser: `8 March Street` без отдельного дома неоднозначно с house-first, а с отдельным `house 12` имя `8 March` сохраняется.

> **Статус честно.** Это релиз-кандидат, проверенный на синтетических и самостоятельно размеченных данных. Точность на скрытом наборе жюри **неизвестна** (не измерена). Справочник — публичные административные имена (91 город, 22 района в 5 городах, 14 микрорайонов Алматы), **без улиц и домов**. Подробности и цифры — в [docs/ACCURACY.md](docs/ACCURACY.md) и разделе «Ограничения».

**Демо онлайн:** https://sorts-cooperation-stayed-vision.trycloudflare.com — собрано из `main`. Это временный туннель Cloudflare: адрес может смениться, аутентификации нет, поэтому загружайте только тестовые адреса.

**Ручной ввод адресов в UI:** в поле «Или введите адреса вручную» вставьте адреса по одному на строку и нажмите **Разобрать текст** (или Ctrl+Enter). Поле добавлено после релиза `v1.0.0-rc3` (коммит `0aeb734`): оно есть при сборке из исходников (`docker compose up -d --build`) и на демо-стенде, но **не входит** в готовый образ `.tar` из GitHub Release `v1.0.0-rc3`. API и парсер не менялись.

## 1. Запуск

> Release `v1.0.0-rc3`: release gate **APPROVED_WITH_WAIVER** (решение владельца: независимый GLM 5.3 Flash — PASS; Haiku review не выполнен, OpenCode Go — HTTP 429). C3/C7: [docs/CONTRACT_RELEASE.md](docs/CONTRACT_RELEASE.md); системный сбой — HTTP 500, а не успешный пустой ответ. Source manifest (SHA256 всех файлов), Docker image ID и `.tar.sha256` приложены к GitHub Release `v1.0.0-rc3`.

Нужно: Docker Engine и Docker Compose v2, платформа **linux/amd64**. Приложению во время работы **не нужен интернет**: нет CDN, внешних шрифтов, облачных моделей и геокодеров.

### 1а. Офлайн-запуск из готового образа (основной путь для проверки)

Образ `jana-address:team67-rc3-final` собирается заранее и передаётся файлом `jana-address-team67-rc3-final.tar` (файл и `jana-address-team67-rc3-final.tar.sha256` — в GitHub Release `v1.0.0-rc3`; положить оба файла в корень репозитория).

```bash
sha256sum -c jana-address-team67-rc3-final.tar.sha256     # проверка целостности
docker load -i jana-address-team67-rc3-final.tar          # один раз, сеть не нужна
docker compose up -d --no-build --pull never         # ничего не собирает и не скачивает
```

Затем открыть **http://127.0.0.1:8000/** (UI) и проверить `curl -sS http://127.0.0.1:8000/health`.

Остановка: `docker compose down`.

### 1б. Сборка из исходников (нужен интернет один раз)

```bash
docker compose up -d --build
```

Сборка тянет базовый образ `python:3.12-slim` и пакеты из PyPI (`requirements.txt`). **Офлайн-сборка невозможна** — это проверено: `docker build --network none` падает на `pip install` («No matching distribution found for fastapi==0.115.12»). Поэтому для среды без интернета нужен заранее собранный образ из п. 1а.

### 1в. Порт и адрес

По умолчанию сервис слушает только `127.0.0.1:8000` (аутентификации нет). Если порт занят или нужен доступ с другой машины доверенной сети:

```bash
JANA_PORT=8010 docker compose up -d                 # другой порт
JANA_BIND=0.0.0.0 docker compose up -d              # доступ из сети, БЕЗ аутентификации — только в доверенной сети
```

## 2. Требования к заранее собранному образу

| Что | Значение |
|---|---|
| Имя и тег (должны совпадать с `compose.yaml`) | `jana-address:team67-rc3-final` |
| Платформа | linux/amd64 (на arm64 образ нужно пересобрать по п. 1б) |
| Размер RC-3 (локально измерен) | Переизмеряется после языковой/licensing стабилизации; образ `sha256:94d7949c76af36187a442678a8637a8eaa026c31ea243a05d6a343bf0694b9db`, tar 48 496 640 байт (SHA256 — в `.tar.sha256` в GitHub Release) |
| Содержимое | `/srv/jana`: `app/` (код и статика UI), `data/gazetteer.json`, `data/geo/MANIFEST.json`, `data/demo_input.json`, `NOTICE.md`, `requirements.txt`, `docs/geo-provenance/aksay-osm-snapshot.json`. Нет: тестов, скриптов, `train*`, `holdout*`, `data/qa`, `.git`, секретов |
| Процесс | пользователь `appuser` (uid 10001), `uvicorn app.main:app`, 1 worker, порт 8000 в контейнере; файловая система только для чтения, `/tmp` в tmpfs |
| Compose | при наличии образа с таким тегом `docker compose up` **не пересобирает** его и ничего не скачивает |
| Проверка | `curl -sS http://127.0.0.1:8000/health` → `"ready":true` |

Если образа с этим тегом нет, `docker compose up` попытается собрать его и без интернета упадёт — сначала выполните `docker load`.

## 3. Проверка за минуту (smoke)

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS -X POST http://127.0.0.1:8000/api/v1/addresses/parse-batch \
     -H 'Content-Type: application/json' --data-binary @data/demo_input.json
curl -sS -X POST http://127.0.0.1:8000/api/v1/addresses/upload -F 'file=@data/demo_input.json'
```

Пример `/health` (реальный ответ):

```json
{
 "status": "ok",
 "offline_runtime": true,
 "cities_in_local_seed": 91,
 "gazetteer_type": "curated public administrative gazetteer (offline runtime)",
 "version": "0.1.0",
 "ready": true,
 "error_count": 0,
 "gazetteer": {
  "cities": 91,
  "provinces": 20,
  "living_areas": 14
 }
}
```

В интерфейсе: **Загрузить демо**, загрузить файл или ввести адреса вручную в поле «Или введите адреса вручную» (по одному на строку, кнопка **Разобрать текст** или Ctrl+Enter; id назначаются как `T1`, `T2`, …) → таблица из 14 полей → фильтр «На проверку» по низкому confidence → править поле (id и raw_address заблокированы) → Reset → **Экспорт JSON / CSV**.

## 4. REST API

Основной маршрут: `POST /api/v1/addresses/parse-batch` (алиасы: `POST /api/parse-batch`, `POST /parse`). Загрузка файла: `POST /api/v1/addresses/upload` (multipart, поле `file`, JSON или JSONL, ≤ 8 MiB). Служебные: `GET /health`, `GET /openapi.json`, `GET /api/v1/addresses/demo`. Страница `/docs` (Swagger) **отключена намеренно** — она грузит скрипты с CDN; схема доступна на `/openapi.json`.

> Транспорт и формат запроса организаторами в ТЗ не определены. Мы приняли формат примера из ТЗ (массив `{"id","raw_address"}`) и **временный** контракт v1.1; он не подтверждён организаторами.

**Вход.** JSON-массив записей `{"id": <строка|целое>, "raw_address": <строка>}`. Также принимаются обёртки `{"items":[…]}`, `{"records":[…]}`, `{"addresses":[…]}` и одиночная запись. Лишние поля игнорируются. Ответ повторяет форму входа.

**Выход.** Ровно по одному объекту на каждую входную запись, в том же порядке (дубликаты `id` не склеиваются), ровно 14 полей: `id, raw_address, province, city, district, living_area, street_type, street_name, house, apartment, entrance, floor, landmark, confidence`. Неизвестное значение — JSON `null`. `house`, `apartment`, `entrance`, `floor` — всегда строки (`12а`, `33/1`, `100/2` не превращаются в числа). `raw_address` возвращается без изменений, `street_name` — так, как написано во входе (опечатки и падеж не исправляются). `confidence` — эвристика от 0 до 1, **не** калиброванная вероятность и не точность.

Пример запроса и реального ответа:

```json
[
 {
  "id": "d1",
  "raw_address": "г Семей ул Мира д 3 кв 4 подъезд 2 этаж 3"
 },
 {
  "id": 2,
  "raw_address": "Тараз Жамбыла 100/2 кв.4 3-этаж"
 }
]
```

```json
[
 {
  "id": "d1",
  "raw_address": "г Семей ул Мира д 3 кв 4 подъезд 2 этаж 3",
  "province": "Абайская область",
  "city": "Семей",
  "district": null,
  "living_area": null,
  "street_type": "улица",
  "street_name": "Мира",
  "house": "3",
  "apartment": "4",
  "entrance": "2",
  "floor": "3",
  "landmark": null,
  "confidence": 0.93
 },
 {
  "id": 2,
  "raw_address": "Тараз Жамбыла 100/2 кв.4 3-этаж",
  "province": "Жамбылская область",
  "city": "Тараз",
  "district": null,
  "living_area": null,
  "street_type": null,
  "street_name": "Жамбыла",
  "house": "100/2",
  "apartment": "4",
  "entrance": null,
  "floor": "3",
  "landmark": null,
  "confidence": 0.85
 }
]
```

### Невалидные записи (контракт v1.1, item-level)

Одна плохая запись **не отклоняет** весь batch. Для записи, у которой нет `raw_address`-строки, `raw_address` длиннее 3000 символов, либо элемент — не объект, сервис возвращает 14-польный объект: `id` как во входе (или `null`, если он не строка/целое), `raw_address` как во входе, если это строка (включая строку > 3000 символов у fallback), иначе `null`, остальные поля `null`, `confidence: 0.0`. Если таких записей N ≥ 1, в ответе есть заголовок `X-Jana-Invalid-Items: N`.

```json
[
 {
  "id": "ok",
  "raw_address": "ул. Мира 13б кв. 5"
 },
 {
  "id": "bad"
 }
]
```

```text
x-jana-invalid-items: 1
[{"id":"ok","raw_address":"ул. Мира 13б кв. 5","province":null,"city":null,"district":null,"living_area":null,"street_type":"улица","street_name":"Мира","house":"13б","apartment":"5","entrance":null,"floor":null,"landmark":null,"confidence":0.64},{"id":"bad","raw_address":null,"province":null,"city":null,"district":null,"living_area":null,"street_type":null,"street_name":null,"house":null,"apartment":null,"entrance":null,"floor":null,"landmark":null,"confidence":0.0}]
```

Ошибки всего запроса (на весь запрос, без эха содержимого): `400 invalid_json` (битый JSON, невалидный UTF-8, слишком глубокая вложенность), `422 invalid_envelope` (неизвестная форма), `413` (больше 10 000 записей, тело > 32 MiB, файл > 8 MiB), `400 invalid_jsonl` + номер строки.

## 5. Восстановление после сбоя

| Симптом | Что сделать |
|---|---|
| `docker compose up` пытается собирать образ и падает на `pip` | Образа `jana-address:team67-rc3-final` нет локально: `docker load -i jana-address-team67-rc3-final.tar`, затем `docker compose up -d --no-build --pull never` |
| `port is already allocated` | Порт занят: `JANA_PORT=8010 docker compose up -d` и открыть `:8010` |
| Страница не открывается / `curl` не отвечает | `docker compose ps` (статус должен быть `healthy`); `docker compose logs --tail=100`; перезапуск: `docker compose restart` |
| Контейнер завис или в `unhealthy` | `docker compose up -d --force-recreate` |
| Нужно «чистое» состояние | `docker compose down && docker compose up -d` (приложение ничего не хранит на диске — состояния нет) |
| API вернул HTTP 500 / растёт `/health.error_count` | Внутренний сбой, а не нераспознанный адрес: запрос не получает успешный partial response. Стабильное тело `{"detail":{"code":"internal_error"}}`; событие `parse_system_failure` содержит только тип исключения, без payload/сообщения исключения. Исправить причину, затем повторить исходный batch. |
| Ответ 413 / 400 / 422 | Это ошибка запроса целиком (см. п. 4), а не сбой сервиса; исправьте файл |
| В UI пропали правки | Правки живут в памяти вкладки; перед закрытием — «Экспорт» |
| Образ повреждён | сверить `sha256sum -c …tar.sha256`, повторить `docker load`; крайний случай — п. 1б с интернетом |

## 6. Тесты и воспроизводимость

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest==9.1.1 httpx==0.28.1      # нужен интернет; UI-тесты используют Chrome/Chromium и Node ≥ 22
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider
```

Предыдущий RC-2 имел заявленный прогон 1161 passed / 2 skipped. Эти числа **не являются приёмкой RC-3**. Актуальные результаты, source manifest, Docker image ID и условия замеров RC-3 : 1321 passed / 2 skipped на Python 3.12 и 3.14; source manifest и image ID — в GitHub Release `v1.0.0-rc3`. Два исторических `adjudication-pending` не считать PASS: нумерация микрорайонов и заполнение адреса по POI ожидают решения организаторов.

```
tests/test_parser.py, tests/test_geo.py, tests/test_api.py  — unit / API / справочник
tests/test_poi_guard.py                                     — POI никогда не заполняет адресные поля
tests/qa/                                                   — независимые QA-тесты: контракт, RU/KZ, литеры и дроби, детерминизм, negative
tests/ui/                                                   — E2E в Chrome: загрузка, правка, reset, экспорт JSON/CSV
```

Замеры времени (методика и результаты): `scripts/release/release_acceptance.py` (холодный старт контейнера до реальной готовности, REST-прогон 1000 записей, проверка работы в контейнере без сети). Сборка образа в «холодный старт» не входит и измеряется отдельно.

## 7. Как устроено

Фактический конвейер `app/parser.py`: неизменный ввод и scan-view с сохранением offsets → явные область/город с отсечением street-контекста → район → landmark → детали квартиры/подъезда/этажа/дома → тип и название улицы/микрорайона → однозначное локальное geo-enrichment → эвристическая уверенность. Правила: без кэша по `id` или по строке целиком, без сетевых вызовов, одинаковый вход даёт одинаковый результат. Время разбора ограничено (регулярные выражения защищены от катастрофического перебора; тест на патологические входы ≤ 3000 символов).

Справочник `data/gazetteer.json`: у каждой записи есть источник и лицензия, сводка и контрольные суммы — `data/geo/MANIFEST.json`, атрибуция — `NOTICE.md`.

## 8. Ограничения (без приукрашивания)

- **Нет данных об улицах и домах.** Название улицы берётся из строки; справочник улиц не используется. Район и область определяются из справочника только при однозначном совпадении, иначе `null`.
- **Магазины / POI.** В справочнике 4 *синтетических* записи для тестов, это не покрытие реальных сетей. Парсер **никогда** не подставляет адрес из POI: название магазина даёт только `landmark`; город и область берутся только из самой строки.
- **Точность.** Скрытый набор жюри не измерен. Наши оценки — на небольших размеченных нами выборках, см. [docs/ACCURACY.md](docs/ACCURACY.md). Эвристический `confidence` не калиброван.
- Неоднозначные строки (например, `Актау 15 мкр 22 дом 5 кв 8`: какое из чисел — номер микрорайона) разбираются консервативно: `street_name` остаётся `null`.
- Казахское написание города без русской формы (`Қарағанды`) — политика канонического названия не подтверждена организаторами.
- Аутентификации и ограничения частоты нет: сервис рассчитан на локальный запуск.
- Лицензии: Википедия CC BY-SA 4.0 и OpenStreetMap ODbL — см. `NOTICE.md`; окончательное решение о распространении данных — за владельцем команды.

## 9. Приватность

В репозитории и образе нет данных организаторов, закрытых наборов и секретов; логи не содержат текстов адресов. Приложение не пишет данные на диск.

## 10. Состав репозитория

```text
app/                 main.py (REST), parser.py (разбор), geo.py (справочник), static/ (UI)
data/                gazetteer.json, geo/MANIFEST.json, demo_input.json (+ синтетические файлы для тестов, не входят в образ)
scripts/             import_gazetteer_csv.py, release/release_acceptance.py, eval_holdout.py (метрики) и др.
tests/               unit, API, справочник, UI (Chrome), независимые QA-тесты tests/qa
docs/ACCURACY.md     честная таблица оценок по полям с оговорками
Dockerfile, compose.yaml, requirements.txt, NOTICE.md
```

`Caddyfile` и `compose.public.yaml` — необязательный публичный HTTPS-прокси для демонстрации; в проверяемый запуск не входят.
