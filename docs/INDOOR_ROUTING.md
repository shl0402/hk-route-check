# LandsD indoor maps and station routing

## Reproduce from source

Install the pinned `requirements.txt` (or `requirements-routing.txt` for the
source importer alone). The importer uses Python, pyogrio/GDAL, pyproj and
Shapely; no desktop GIS or proprietary ArcGIS installation is needed.

The normal `python3 run.py build` workflow now downloads/verifies both LandsD
indoor sources and compiles indoor pathways before GTFS validation and OTP.
`--offline` verifies and reuses cached sources. `--refresh` explicitly replaces
source snapshots. Export/import through `run.py export-cache` includes the
original network FGDB, comparison GeoJSON, map layers and their manifests.
Downloaded data remain ignored by Git. Follow DATA_SOURCES.md for redistribution.

To inspect just the indoor stage, without changing a running server:

```sh
.venv/bin/python scripts/landsd_enrich.py fetch
.venv/bin/python scripts/indoor_network.py fetch
.venv/bin/python scripts/landsd_enrich.py merge
.venv/bin/python scripts/indoor_network.py compile
.venv/bin/python -m unittest discover -s tests -p test_indoor_network.py
```

The merge command requires the normal build's `hk-transit-MTR-API.gtfs.zip`.
The indoor compiler accepts `--input` and `--output` for isolated candidate feeds.
An enriched ZIP is not an active update until the matching OTP graph is rebuilt,
independently validated and installed with its release manifest. Stop servers
before changing active files. Start the app yourself after installation.

For the historical example:

```sh
python3 run.py build --offline --start-date 2026-09-17 --days 30
python3 run.py check
```

## Source and transformation rules

- `scripts/landsd_enrich.py` downloads all 98 published MTR venues and all five
  interior layers, with response-count validation and SHA-256 source manifests.
- `scripts/indoor_network.py` downloads the original FGDB and comparison GeoJSON.
  Every original table and all companion domain definitions are retained.
- The original network has HK1980 horizontal coordinates and HKPD height.
  Only X/Y are transformed to WGS84; Z remains metres above HKPD, **not GPS altitude**.
- Source `PedestrianRouteID` values cross-check the two network formats. The
  comparison reports missing IDs, direction/access/type differences, geometry
  discrepancies and table counts. The original format is authoritative.
- The GeoJSON export contains eight lift paths reduced to points. Original 3D
  line geometry is used, preserving the endpoints at different heights.
- Floor relationships join by published `FloorID`. Alternate `FloorPolyID`
  strings in the two exports are retained as aliases for that same numeric ID;
  disagreements are reported rather than silently stripped or guessed.
- Map landmarks may bind to a unique vertex within 25 cm horizontally and
  5 mm vertically. Network endpoints differing by at most 1 cm horizontally
  and 5 mm vertically are normalized deterministically. These tolerances repair
  precision differences, not missing corridors or floor connections.
- Source segments on intermediate levels are retained when their names identify
  that same station. A source landing absent from the map gets a relative numeric
  level index from its height, but no invented floor name.
- Entrance matching uses published exit codes, Unicode normalization, ground/street
  labels, and an independent iGeoCom position check within 40 m. An anonymous
  iGeoCom station lift requires a unique ground/street lift within 10 m.

## What enters routing

The original MTR feed has logical stops per station/line. The importer now creates
separate physical platform-area stops and assigns verified trip endpoints to them.
It uses cached official MTR responses: explicit departure platform numbers for the
next station, and unanimous station/line/Track observations for arrival platforms.
Track numbers are never treated as globally unique. Every response and the line
inventory are hashed in the report. Contradictory or unknown evidence is withheld.
Where numbered platforms share one mapped unit, they can use that shared area;
platforms in separate units are never merged merely because the station is the same.

The compiler preserves the original logical stops and their parents for unresolved
trip endpoints. Verified physical platforms and entrances use a separate parent for
that physical station, including stations shared by Airport Express and Tung Chung
Line. This avoids connecting platforms across lines by guessing. Unresolved calls
keep their previous access behavior and are counted explicitly in the report.

A physical platform must have at least one verified entrance with a permitted path
in both directions. One bad entrance no longer excludes the whole station. Stations
are reported as complete (`activated`), partial, or withheld; inspect exclusions
before claiming that a particular entrance is supported. Both complete and partial
stations contribute the union of their verified paths to standard GTFS pathways.
All 98 indoor maps remain available, including those withheld from routing.

Continuous lift edges separated only by degree-two points are combined into one ride,
retaining all source IDs and one assumed wait. Landings with connecting walkways are
preserved. Disabled paths, unsupported types and time-restricted passages are excluded.
`AccessTime` means opening hours, which static GTFS pathways cannot represent.
Source geometry, restrictions, accessibility flags and full maps remain available.

Movement durations are **model estimates**, not measured LandsD walking times:
1.2 m/s walking, 0.6 m/s stairs, 0.5 m/s escalators; lifts use 1 m/s vertical speed
plus a 20-second assumed wait per continuous ride. The cost model uses 3D geometry;
GTFS `length` records horizontal distance as the specification requires. Each edge
rounds up to whole seconds; co-located binding edges take one second. Existing train
boarding slack is separate. Live lift outages, crowds and reliable wheelchair
navigation are not claimed. Only verified `stop_id` fields change in `stop_times.txt`;
train times, trip IDs, frequencies and other timetable fields are preserved.

## Outputs and APIs

- `data/landsd/raw/`: original downloads, URLs, timestamps, sizes and hashes.
- `data/landsd/indoor/*.geojson`: every network table/domain and combined map layer.
- `data/landsd/indoor/validation.json`: cross-source findings, station exclusions,
  exact bindings, directed entrance/platform paths, estimated times and source IDs.
- `data/landsd/indoor/stations/<venue-id>.json`: station floor/unit/door/facility
  layers and its full associated network, including non-routing reference data.
- `GET /api/indoor/stations`: station availability and routing eligibility.
- `GET /api/indoor/stations/<venue-id>`: station map package. The native w8g server
  uses its existing sign-in requirement for these routes.
- `indoor_provenance.json` inside GTFS: the validation report tied to the feed.

Swift needs a separate UI step to render these indoor layers and floor controls.
The server integration itself does not change the app's visible map style.

## Validation and release

Do not equate passing GTFS syntax checks with accurate real-world walking time.
Check directed topology, known entrance-to-platform pairs and live OTP access
legs, including reverse travel, additional MTR stations and bus alternatives.
The LOHAS Park regression uses published entrance coordinates and asserts that
the former kilometre-scale access detour is absent. It does not require matching
Google's exact estimated minutes.

The same source code, data, feed, graph, configuration and manifest must be
installed in map_routing and w8g. Never copy accounts, credentials, databases,
Swift files or the retired HTML prototype into a routing release. Matching
checksums on disk do not update a running OTP: it must be restarted.

Prepare a portable w8g update after the upstream build and routing tests pass:

```sh
.venv/bin/python scripts/package_indoor_release.py --target /path/to/w8g --output /path/to/release
python3 /path/to/release/apply.py --target /path/to/w8g --check
# After stopping w8g yourself:
python3 /path/to/release/apply.py --target /path/to/w8g
```

The installer checks candidate hashes and expected-old target hashes before
changing anything, refuses running API/OTP ports and retains recoverable backups.
It mounts the data endpoints inside w8g's existing authenticated GET handler.

The embedded server receives the import/matching scripts, LandsD sources, cached MTR platform evidence and the exact
pre-indoor base feed with its checksum. Within w8g:

```sh
.venv/bin/python -m pip install -r requirements-indoor.txt
.venv/bin/python scripts/rebuild_indoor.py
```

This produces an isolated, validated candidate under `data/indoor-rebuild/`;
it never overwrites active data or starts a server. Java 25+ must be on PATH.
Use `--refresh` only to deliberately update the public indoor sources.
Regenerating the entire transit timetable remains the upstream `run.py build`
workflow; the indoor-only rebuild deliberately preserves the bundled timetable.

## Why five stations remain withheld

These are unresolved with the current sources, not necessarily unfixable forever.

| Station | Evidence gap | What would allow activation |
|---|---|---|
| Airport | Operator offers platforms 1/3 or 2/4; LandsD network/map covers only 1/2. The current official layout shows four platforms. | Updated Terminal 2 platform and entrance geometry, with operator assignments. |
| Racecourse | No served station/trips in this feed; service is special/race-day dependent. | A reproducible dated race-day timetable and stop mapping. |
| Lo Wu | Operator reports 1 or 4; those are separate mapped platform areas. Arrival evidence is unresolved. | Direction/service-specific platform evidence and verified access restrictions. |
| Lok Ma Chau | Platform area exists, but no matching entrance amenities in the indoor map. | Verified public entrance geometry and links. Its schematic ground floor includes permit-only access. |
| Fortress Hill | Exit A differs from the nearby network height by about 1.67 m; Exit B belongs to a disconnected component with an approximately 1 m gap. | Corrected source topology or a documented, independently checked connector. |

The 1 m Fortress Hill gap is deliberately not repaired by enlarging the 1 cm
precision tolerance. Nearby source floor polygons do not establish a walkable bridge.
By comparison, HKU C1's 2 mm same-height endpoint mismatch is within that tolerance
and is now connected. See `validation.json` for every remaining excluded entrance.

Source checks: [GTFS pathways and levels](https://gtfs.org/documentation/schedule/reference/),
[MTR Airport layout](https://www.mtr.com.hk/archive/en/services/layouts/air.pdf),
[Lo Wu layout](https://www.mtr.com.hk/archive/en/services/layouts/low.pdf),
[Lok Ma Chau layout](https://www.mtr.com.hk/archive/en/services/layouts/lmc.pdf),
[Hong Kong Tourism Board racecourse service guide](https://www.discoverhongkong.com/eng/culture/hong-kong-horse-racing/racecourse-transit-guide.html).
Layouts reviewed on 29 September 2026; source editions respectively March 2026,
February 2024 and July 2025. The PDFs informed review, not fabricated network edges.
