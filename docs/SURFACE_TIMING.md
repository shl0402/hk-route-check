# Surface-transit timing and route paths

## Why this stage exists

The TD headway GTFS has blank intermediate times on 2,366 of 2,386 bus/minibus
route records in our September 2026 baseline. OTP 2.9 interpolates those gaps by
stop count. This assigned the long 690S harbour crossing the same 3.5 minutes as
a short local hop. The defect affects routing/transfer choices, not just labels.

## Source research (30 September 2026)

- TD headway GTFS and specification: https://data.gov.hk/en-data/dataset/hk-td-tis_11-pt-headway-en
- KMB/LWB API: https://data.gov.hk/en-data/dataset/hk-td-tis_21-etakmb
- Citybus API: https://data.gov.hk/en-data/dataset/ctb-eta-transport-realtime-eta
- Green minibus API: https://data.gov.hk/en-data/dataset/hk-td-sm_7-real-time-arrival-data-of-gmb
- Citybus explains its own use of historical journey times, timetable and ETA:
  https://www.citybus.com.hk/en/uploadedfiles/app_guide/en.html
- TD traffic forecasts: https://data.gov.hk/en-data/dataset/hk-td-tis_28-traffic-data-tdas/resource/14ca29a1-a872-4a94-8cc7-7efac9d036c1

No complete downloadable, measured stop-to-stop bus timing table was identified
in these sources. Live ETAs are predictions for upcoming vehicles, not an
arbitrary future timetable; subtracting unrelated ETAs at two stops is unsafe.
Traffic forecasts are for driving routes, not bus stopping/boarding times.

Route geometry comes directly from the Hong Kong Transport Department through
CSDI. Source discovery was informed by https://github.com/hkbus/route-waypoints;
this implementation downloads the official source archives, not that project's
converted geometry or code. Credit: Hong Kong Transport Department / CSDI.

- Bus dataset: td_rcd_1638844988873_41214
- GMB dataset: td_rcd_1697082463580_57453
- Official metadata: https://portal.csdi.gov.hk/geoportal/rest/metadata/item/{dataset}

Original ZIPs, metadata, retrieval times and SHA-256 hashes are retained under
`data/surface/raw`. Offline builds verify those hashes. A refresh can change the
source vintage; its resulting geometry is always revalidated against feed stops.

## Algorithm and limits

1. Match the exact government route ID and first/last stop IDs.
2. Orient and join only connected geometry. Match every intermediate stop in
   order, including repeated roads and circular routes. Each stop must be within
   100 m of the source path. Simplify geometry by at most 1 m.
3. Between each pair of published timing anchors, allocate elapsed time in
   proportion to along-route distance. Retain existing arrival/departure values,
   dwell at published anchors, service calendars and departure headways.
4. Mark filled stop times `timepoint=0`. Published journey duration implicitly
   includes stopping time; no invented traffic or dwell surcharge is added.
5. Add GTFS shapes and matching cumulative distances for validated paths. OTP
   returns those road-following geometries to both web and Swift clients.
6. Where no validated path exists, use explicitly labelled straight-line
   distance weighting (including tram/ferry services without a path source).
   Do not publish that fallback as a verified road shape. MTR/Light Rail timings
   and indoor walking pathways are untouched.

These remain estimates: uniform speed within each anchor interval does not model
traffic, signals or different road speeds. A 49-minute published whole-trip time
cannot be made traffic-aware by geometry alone. Short sections may still be too
fast where dwell dominates. Future measured data can supply additional timing
anchors; geometry does not replace that evidence.

`surface_timing_provenance.json` inside the feed records model, sources, per-trip
pattern references, matching offsets and fallback reasons. Server responses
expose `surfaceTiming`, `timingModelKind`, `geometrySource`, and warnings.

## Reproduce in map_routing

Prerequisites and pinned Python/Java dependencies: see SETUP.md.

```sh
python3 run.py fetch
python3 run.py build --offline --start-date 2026-09-17 --days 30
python3 run.py check
.venv/bin/python scripts/check_source_tests.py
```

The surface stage runs after the indoor stage and before GTFS validation/OTP
compilation. Choose an appropriate service date range for a new deployment.
`run.py export-cache` includes the source ZIPs and manifest for other machines.

To inspect a candidate without replacing the active feed:

```sh
.venv/bin/python scripts/surface_timing.py merge \
  --input data/build-work/data/generated/hk-transit-INDOOR.gtfs.zip \
  --output /tmp/surface-candidate.gtfs.zip
```

## Reproduce independently inside w8g

The routing release includes the pre-surface feed, checked source archives,
source code, validator and existing OTP/OSM inputs. No app_prototype or
map_routing checkout is needed.

```sh
server/.venv/bin/python scripts/rebuild_surface.py
```

This creates `data/surface-rebuild`, validates it and builds a candidate graph.
It does not start/stop a server or replace active files. `--refresh` downloads
new public path data; omit it for exact source reuse. The existing
`rebuild_indoor.py` also reapplies surface timings after rebuilding interiors.

Installation uses the checksummed routing-release installer after stopping the
native server; all replaced files are backed up. Restart the server yourself.
Old saved routes remain snapshots; search again to get corrected timings/shapes.

## October 2026 update

The current pipeline validates **3,261** road shapes: 2,125 bus and 1,136
minibus patterns. Preserving exactly connected source sections recovers 182
previously rejected circular/repeated-road patterns, including all 63 N796
stops. No previous shape match was lost. There remain 250 bus/minibus patterns
without a validated road path, plus 106 ferry and 12 tram patterns.

Official minibus timetable compilation now precedes surface interpolation;
checked historical ETA proportions follow it. See [source enrichment and
current counts](TRANSIT_ENRICHMENT.md). These timings remain estimates between
protected published anchors.

## Previous September 2026 build

- 1,710,083 missing stop rows filled; 1,875,373 existing timed rows preserved.
- All 1,666,541 MTR/Light Rail stop-time records preserved.
- 3,079 validated road shapes: 1,943 bus and 1,136 minibus stop sequences.
- 432 bus/minibus stop sequences retain the explicit straight-line fallback;
  106 ferry and 12 tram sequences also lack a validated source path.
- Independent GTFS validator: zero errors. Original and standalone rebuilds
  produce byte-identical contents for all 23 feed entries (ZIP container
  timestamps need not match).
- Live OTP check, 30 September: 690S stop 8146 -> 18 = 31 minutes, 12.589 km;
  10P stop 20014890 -> 20000008 = 11 minutes, 5.169 km. Both return curved route
  polylines. These are model outputs, not measured peak-traffic journey times.

Run the read-only planner checks against a separately started candidate engine:

```sh
.venv/bin/python route_checker/check_surface_routes.py --otp-port 8193
.venv/bin/python route_checker/check_mtr_routes.py --otp-port 8193
```
