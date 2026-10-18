# Аксай: независимое подтверждение и границы лицензирования

## Результат
Названия двух целевых suburb-объектов независимо получены из OpenStreetMap 18.10.2026: «Аксай 3А» (way265577783, version11) и «Аксай 3» (way265577786, version16). Существующие канонические имена «Аксай 3а»/«Аксай 3», алиасы и runtime-поля не изменены.[4]

Проверка административных полигонов в публичных bbox-centre точках обоих объектов вернула Алматы (area3602465058) и Ауэзовский район (area3603072808). Это независимая поддержка прежних связей, но **не доказательство полного вложения полигонов**. У «Аксай 3» district остаётся null: вне цели этой стабилизации менять значения справочника.[5][6]

История прежнего illustrative seed сохранена в provenance; она не превращена задним числом в OSM-источник. Удалён только ранее использованный registry-key jana-sample, добавлен osm-aksay-targets. Все нетаргетные записи и остальные registry sources эквивалентны проверенной базе; portable тест фиксирует их canonical-JSON checksum.

## Файлы доказательств
`docs/geo-provenance/aksay-osm-snapshot.json`: только две целевые записи с точными id/version/timestamp/name tags/center, bounded query/URL, hash исходного ответа и два representative-point запроса с результатами. Полный публичный ответ сохранён в рабочем evidence, а не добавлен в геосправочник.

`retrieved_at` первого запроса — наблюдаемое UTC filesystem mtime сохранённого HTTP200-ответа (точное начало HTTP-запроса не записывалось). `timestamp_osm_base` — отдельное время снимка сервера; оно **не** выдано за время скачивания. У admin checks fetched_at записан сразу после получения ответа.

## Условия, не юридическое заключение
OSM допускает копирование/распространение с указанием OpenStreetMap contributors; применяются условия ODbL, включая share-alike для соответствующей производной базы.[1] NOTICE и MANIFEST одинаково указывают allowed_with_attribution_and_share_alike, не безусловное разрешение. Изменения и локальный источник указаны; NOTICE и выбранное evidence включаются в Docker image. Остальные источники и данные не пересматривались.

Это закрывает прежнюю неподтверждённость двух названий на уровне инженерной provenance-проверки, а не даёт юридическую гарантию совместимости всего смешанного набора Wikipedia/OSM. Решение о перераспространении/лицензии объединённой поставки остаётся отдельным решением владельца. Нет заявлений о полноте национального адресного реестра, геометрической валидации всей базы или hidden accuracy.

## Sources

[1] https://www.openstreetmap.org/copyright — OpenStreetMap data copyright and ODbL redistribution conditions
[4] https://overpass-api.de/api/interpreter?data=%5Bout%3Ajson%5D%5Btimeout%3A20%5D%3Bnwr%5B%22name%22~%22%28%D0%90%D0%BA%D1%81%D0%B0%D0%B9%7C%D0%90%D2%9B%D1%81%D0%B0%D0%B9%7CAksay%7CAqsai%29.%7B0%2C3%7D3%22%2Ci%5D%2843.10%2C76.60%2C43.45%2C77.20%29%3Bout+meta+center%3B — Actual Overpass API evidence for Aksay3 and3A
[5] https://overpass-api.de/api/interpreter?data=%5Bout%3Ajson%5D%5Btimeout%3A20%5D%3Bis_in%2843.2356852%2C76.8266167%29-%3E.a%3Barea.a%5B%22boundary%22%3D%22administrative%22%5D%3Bout+tags%3B — OSM admin polygons at public way 265577783 representative point
[6] https://overpass-api.de/api/interpreter?data=%5Bout%3Ajson%5D%5Btimeout%3A20%5D%3Bis_in%2843.2357198%2C76.8296969%29-%3E.a%3Barea.a%5B%22boundary%22%3D%22administrative%22%5D%3Bout+tags%3B — OSM admin polygons at public way 265577786 representative point
