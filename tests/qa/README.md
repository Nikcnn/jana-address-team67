# tests/qa — независимый QA-набор JANA ADDRESS (W5)

Запуск (cwd = корень копии):

    PYTHONDONTWRITEBYTECODE=1 /srv/dev/repos/jana-address/.venv/bin/python -m pytest -q -p no:cacheprovider tests/qa/

Принципы
- Ожидания берутся из контракта (v1.0 + дельта v1.1: C3-v1.1 «item-level» заменяет «atomic reject») и текстов задач W1–W3, НЕ из вывода парсера. Дефект baseline = обычный FAIL
  (RED-статус — это ценность), после исправления тест обязан стать GREEN без правок.
- Маркеры: `defect(id)` — тест закрепляет известный дефект из W5_REPORT; `soft` — ожидание выведено QA по аналогии
  (не цитата контракта); `pending` — adjudication-pending / фича ещё не интегрирована (skip с причиной, gold не выдумывается);
  `rest` — поднимает реальный uvicorn на 127.0.0.1.
- `time_limit` в helpers.py поднимает BaseException: `parse_batch` ловит `Exception`, и обычное исключение таймера
  превращалось бы в «пустую запись» (ложный PASS). Эта ошибка уже была поймана и исправлена.
- `synthetic_geo` (conftest) подменяет `get_data()` вымышленными городами; если geo строит свой индекс мимо `get_data()`,
  тест skip-ается с причиной, а не падает по ложной причине.

Файлы: test_acceptance (сценарии §4, data/qa/acceptance_scenarios.json), test_conformance (C4–C7, Python-интерфейс),
test_api_contract (C1–C3-v1.1, C7, REST через TestClient; item-level: null-записи, X-Jana-Invalid-Items, 1-из-1000 / все-1000, upload JSON/JSONL), test_house_tokens, test_ru_kz, test_poi_inference (POI и abstention),
test_negative (ReDoS, injection-as-data, граничные входы), test_determinism (рестарт, hash-seed, no-network),
test_eval_harness (проверка самого измерительного скрипта).
