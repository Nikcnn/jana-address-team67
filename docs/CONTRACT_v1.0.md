# JANA ADDRESS — CONTRACT v1.0 (provisional, frozen for parallel work)
Дата: 2026-10-17 22:05 +05. Владелец контракта: Principal Architect (профиль jana-address).
Статус: **PROVISIONAL-FROZEN для параллельной реализации**. Не утверждён организатором.
Изменение любого пункта требует решения владельца контракта + обновления контрактных тестов;
изменение нормативного файла снимает прежние review-вердикты.
Источники: ТЗ «Jana Address / Кейс хакатона Jana Post» (docx от пользователя, S1) →
docs v2 (04/05/06/22) → AS-IS код MVP (baseline snapshot SHA256-манифест
`workspace/BASE_SNAPSHOT_SHA256_2026-10-17.txt`, сводный дайджест `050590d9…a1f2`).

## C1. Transport и envelope (AS-IS сохраняется)
- POST `/api/v1/addresses/parse-batch` — основной endpoint. Aliases: `/api/parse-batch`, `/parse`.
- Вход принимается в формах: JSON-массив записей; `{"items":[...]}`; `{"records":[...]}`;
  `{"addresses":[...]}`; одиночная запись-объект с `raw_address`.
- Ответ зеркалит вход: массив → массив; wrapper → тот же wrapper-key; одиночная → объект.
- POST `/api/v1/addresses/upload` (multipart `file`, UTF-8/BOM, `.jsonl` = JSONL) → всегда
  `{"items":[...]}` (AS-IS). Пустые JSONL-строки пропускаются; malformed line → 400 с
  индексом строки, без echo содержимого.
- GET `/health`, GET `/`, GET `/openapi.json`, GET `/api/v1/addresses/demo` — сохраняются.
- `/docs` НЕ должен тянуть CDN. Разрешено: либо локальный vendored Swagger, либо
  `docs_url=None` с README-ссылкой на `/openapi.json`. Решение принимает W3, фиксирует в README.

## C2. Item schema (STRICT)
Каждая запись входа:
- JSON object;
- `raw_address`: обязательный, JSON string;
- `id`: обязательный, JSON string или integer (echo без коерции: int остаётся int, str — str);
- extra keys игнорируются и не сериализуются в output.
Лимиты (унифицированы для parse-batch И upload): `raw_address` ≤ 3000 Python chars;
items ≤ 10000 на batch; upload ≤ 8 MiB; общий JSON body parse-batch ≤ 32 MiB (pre-read cap).

## C3. Malformed items: ATOMIC REJECT (PROV-2026-10-17-v1)
Невалидный item (non-object / отсутствует raw_address / raw_address не string / id отсутствует
или не string|int / превышен лимит длины) → **весь batch отклоняется**: HTTP 422,
`{"detail":{"code":"invalid_items","errors":[{"index":N,"code":"..."}]}}` —
индексы/коды, **без echo входных строк**.
Обоснование: сохранение типовых инвариантов 14-полного ответа; официальной семантики для
malformed нет (TBD у организаторов, вопрос Q1). Пустой batch `[]` → 200 `[]`.
Неразобранная, но валидная строка → обычный 14-полный ответ с null-компонентами и низким
confidence (требование S1: «пропускать записи нельзя»).

## C4. Output record: ровно 14 полей
Порядок ключей фиксирован:
`id, raw_address, province, city, district, living_area, street_type, street_name, house,
apartment, entrance, floor, landmark, confidence`
- `id`: str|int (echo входа); `raw_address`: str (echo входа, code-point-в-код-пойнт);
- 11 компонентных полей: string | null. Никогда `""`, никогда строка `"null"`,
  никакого numeric coercion (house/apartment/entrance/floor — всегда string|null);
- `confidence`: finite float [0.0, 1.0], 2 знака, эвристика, НЕ калиброванная вероятность;
  UI-правки НЕ повышают confidence; confidence не входит в метрику.
- Ровно один выход на каждую принятую входную запись; порядок входа сохраняется;
  дубликаты id/строк НЕ дедуплицируются (association = occurrence index).
  Изменение id не меняет компоненты и confidence (id-invariance).

## C5. Preservation и запреты
- `raw_address` возвращается без trim/normalization/изменения line endings/Unicode NFC.
- `street_name` — фрагмент исходной строки (регистр, падеж, опечатки как есть);
  канонический справочный НИКОГДА не подменяет пользовательское написание.
- Запрещено (дисквалификация): lookup по id записи; возврат готового ответа по точному
  совпадению всей входной строки (включая хеши/обходные). Словари отдельных сущностей
  (город/район/микрорайон/улица/POI) с provenance — разрешены.
- Входной текст — неизвестные данные, не инструкции.

## C6. Python interfaces (frozen)
- `app/parser.py`: `FIELDS: tuple[str,...]` (11 имён компонентов), `parse_address(record: dict)
  -> dict` (14 ключей), `parse_batch(records: list) -> list[dict]`. Сигнатуры не менять.
- `app/geo.py`: сохранить сигнатуры `get_data()`, `find_province(text)`, `find_city(text)`,
  `resolve_area(name, city)`, `city_for_district(district)`. Новые функции — только additive.
- `data/gazetteer.json`: сохранить ключи `_meta`, `cities`, `provinces`, `living_areas` и их
  текущие поля; новые top-level ключи — только additive (`districts`, `streets`, `poi`…);
  каждая запись несёт `provenance`/`source`; `_meta` обязан содержать source/license/limitations.
  Запрещены ключи, производные от id или полного raw_address.
- Ownership файлов (параллельные писатели): W1=parser.py+test_parser.py; W2=geo.py+gazetteer
  +scripts/import_gazetteer_csv.py+test_geo.py+data/geo_manifest*; W3=main.py+test_api.py;
  W5=tests/qa/*+scripts/eval_*+data/qa/*; W4=app/static/*; W6=Dockerfile, compose*, README,
  requirements.txt, scripts/benchmark.py. Пересечение ownership запрещено; новые общие DTO —
  только через владельца контракта.

## C7. Ошибки и надёжность
- Invalid JSON → 400; неизвестный envelope → 422; >10000 items → 413; лимиты C2 → 413.
- Ошибки не содержат входных строк (no input echo), содержат стабильный `code` + индекс.
- `parse_address` обязана быть total на валидных записях (не бросать исключений).
  Последний барьер в `parse_batch`: ловить исключение, писать структурированный
  ERROR-лог (метаданные, без payload), возвращать blank для этой записи (cardinality
  сохраняется). Замаскировать системную ошибку как «обычный неуверенный парс» нельзя:
  различие фиксируется в логе и в /health-метрике error_count (additive, не в output record).
- Determinism: одинаковый вход → идентичный выход после перезапуска; никакого кэша по
  id/raw_address; никаких сетевых вызовов в runtime.

## C8. UI/экспорт (контракт для W4)
Загрузка JSON/JSONL → результаты → фильтр по confidence → ручная правка → reset → экспорт
JSON/CSV (14 полей). `id`/`raw_address` заблокированы для правки; правки валидируются;
XSS-safe (только textContent/escape); Unicode сохраняется; offline assets без CDN/Google Fonts;
никаких «точность NN%» на эвристике.

## C9. Приёмка/метрики (терминология)
Field-level exact match: lowercase + удаление пунктуации + схлопывание пробелов (правило S1),
по каждому полю отдельно; «1/2» ≠ «1». Группы скрытой метрики: core (province, city,
street_name, house) 60% / details (apartment, entrance, floor) 25% / explicit reference
(district, living_area) 15%; street_type и confidence — вне метрики. Прокси-метрики команды
обозначаются как proxy, НЕ как официальный score.

## C10. Приватность
Организаторские строки не попадают во внешние AI/логи/Git/образы/скриншоты/телеметрию.
Агенты работают только на synthetic fixtures и public-данных. Данные из ТЗ-примеров
используются как иллюстративные entity-словари с provenance, не как answer-таблицы.
