# NOTICE — data attribution and licences (JANA ADDRESS, Team 67)

This notice ships inside the Docker image (`/srv/jana/NOTICE.md`). It describes the third-party material that the packaged reference data (`data/gazetteer.json`, `data/geo/MANIFEST.json`) was compiled from. It is an engineering record, **not legal advice**; the team owner is responsible for the final licence decision before any redistribution beyond the hackathon.

## What the reference data is
A small offline gazetteer of **administrative names only**: 17 oblasts + 3 cities of republican significance, 90 cities (RU / KZ-Cyrillic / Latin aliases), 22 intra-city districts in 5 cities, 14 Almaty microdistrict names, and 4 **synthetic** POI fixtures. It has **no** streets, house numbers, coordinates or geometries and is **not** an official KATO/OSM address register.

## Sources
| Source | Used for | Licence | Attribution |
|---|---|---|---|
| Russian Wikipedia — «Список городов Казахстана» and the city articles for Караганда, Актобе, Алматы, Астана, Шымкент (retrieved 2026-10-17) | city names, Kazakh spellings, oblast affiliation, intra-city district lists | CC BY-SA 4.0 | © Wikipedia contributors, https://creativecommons.org/licenses/by-sa/4.0/ . The compiled tables derived from it are offered under the same licence (share-alike). |
| OpenStreetMap, queried via Overpass API (2026-10-17 and 2026-10-18) | Existence/name evidence for 12 previously checked Almaty areas and two exact target suburb ways: https://www.openstreetmap.org/way/265577783 (version 11) and https://www.openstreetmap.org/way/265577786 (version 16). | ODbL 1.0 | © OpenStreetMap contributors, https://www.openstreetmap.org/copyright . Copy/distribution/adaptation is conditional on attribution and share-alike; see https://opendatacommons.org/licenses/odbl/1-0/ . This notice is not a legal conclusion. |
| Project fixtures | 4 POI rows (`synthetic: true`), test data | project-internal | invented; not real branches or addresses |

## Not included
No GeoNames, KATO or full OSM extract. No organizer training/hidden data, ids or gold answers. No personal data.

## Known licence caveats (open for the owner)
1. The target way tags establish names, not administrative membership. Separate representative-point checks fetched on2026-10-18 put both public way bbox-centres in Алматы (OSM area3602465058) and Ауэзовский район (area3603072808). This supports the existing associations, not whole-polygon containment; it is not full geometry validation. `Аксай 3` district stays null to preserve existing runtime values. Exact queries, observed retrieval times and selected public evidence: `docs/geo-provenance/aksay-osm-snapshot.json`.
2. ODbL attribution and share-alike conditions apply to reuse of OSM data. Confirm the licence treatment of the combined table and any broader redistribution before release; no comprehensive legal clearance is claimed.
3. The earlier illustrative provenance is preserved as history; it has not been retroactively represented as OSM-derived.
4. Per-table provenance, licences, coverage gaps and checksums: `data/geo/MANIFEST.json`.
