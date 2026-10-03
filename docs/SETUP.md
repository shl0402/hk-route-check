# Setup and reproducibility

## Prerequisites

Use Python 3.11+ and Java 25. [Python downloads](https://www.python.org/downloads/) · [Java 25 / OpenJDK](https://jdk.java.net/25/) · [OTP 2.9 requirements](https://docs.opentripplanner.org/en/v2.9.0/Basic-Tutorial/).

- **macOS:** install Python, Java 25 and curl with your preferred package manager; ensure `java -version` shows 25+.
- **Linux / WSL2:** install Python, its `venv` package, curl and Java 25. Use the Linux terminal, not the Python `>>>` prompt. Native Windows is not supported by the file-locking collectors.
- 16 GB RAM and 12 GB free disk are recommended. OTP uses a 4 GB Java heap; Python compilation and parsed wiki evidence also use memory and disk.
- The app runs locally. Map tiles and Leaflet assets require internet even when route data was built offline.

## One workflow

`python3 run.py` builds missing artifacts and starts the app. `python3 run.py build` rebuilds without serving. A previous verified build is reused on a normal start; use `build` when changing source/parser code or service dates.

The sequence is:

1. Verify/download government GTFS, OSM, route metadata, MTR station inventories and service hours.
2. Discover bus/minibus wiki directories; compare candidates with GTFS; cache selected articles. Scrape rail wiki lines and all ordered pairs within each rail system.
3. Parse source evidence and audit route identities; retain original data for ambiguous or unsupported cases.
4. Build base GTFS → accepted bus wiki schedules → rail wiki schedules → complete MTR origin/destination journeys (including service-note-backed same-line changes) → LandsD enrichment → cross-validated indoor station pathways → verified official minibus calendars/timetables → validated surface paths → checked historical ETA proportions between preserved timing anchors → verified Sun Ferry departures and journey ranges.
5. Run the independent GTFS validator, calendar/timing preservation checks and rail merge checks, then build OTP once.
6. Activate the successful feed and graph together; serve the Python API and HTML page.

Build steps run in `data/build-work/`. A failed build does not replace the active graph. `data/setup-progress.json` records completed stages and output checksums. Rerunning verifies source/code fingerprints and reuses valid stages; a changed input reruns dependent work. Stop the app before replacing its active build. The wrapper refuses occupied ports rather than attaching to or killing another server.

Collector progress is printed continuously. The OTP build writes `data/setup-logs/otp-build.log`; GTFS validator output is in `data/build-work/data/generated/validator/`. Read `python3 run.py status` from a second terminal. Some individual parse/compile steps can be quiet for minutes.

## Rebuild and test

After changing GTFS parsers or updating the project, stop the app with Ctrl+C and run:

```sh
python3 run.py build
python3 run.py check
python3 run.py
```

A normal start reuses the verified build; it does not automatically apply new parser code to an old graph. Rebuilding the ZIP alone is insufficient: `build` updates the matching OTP graph too. The two-point and multi-stop pages use that same build. Changes to source timings also invalidate route/matrix caches.

To reproduce the current historical example from a complete local source cache and installed dependencies:

```sh
python3 run.py build --offline --start-date 2026-09-17 --days 30
python3 run.py check
python3 run.py
```

Open `/` for the two-point checker or `/multi` for worker schedules. For a quick route check, choose LOHAS Park → HKU, **26 September 2026, 07:32**, and enable Show alternatives. The cached MTR rail journey via Tseung Kwan O and North Point is 37 minutes; access/egress and other options depend on the selected locations.

Also check **29 September 2026, 11:00**. The fast 37-minute MTR journey must remain available when slower MTR paths depart at the same time. With the reference LOHAS Park/HKU coordinates in the regression script, this is about 50 minutes including walking and waiting. This is a cached timetable result, not a live arrival promise.

Source-only tests (after dependencies are installed):

```sh
.venv/bin/python scripts/check_source_tests.py
```

Live MTR checks for the September snapshot, with the server running:

```sh
.venv/bin/python -m unittest discover -s route_checker -p test_mtr_od_live.py
.venv/bin/python -B route_checker/check_mtr_routes.py --otp-port 8081
.venv/bin/python -B route_checker/check_routing_regressions.py
```

The second check reads this checkout's feed and queries its running OTP directly; it does not create jobs or history. It checks every synthetic MTR trip's duration-separated route identity, weekday/weekend LOHAS Park–HKU, the reverse direction, three other station pairs, fastest ranking and bus alternatives. Dates default to the September snapshot; use `--weekday` and `--saturday` only with appropriate in-window dates and compatible cached operator timings.

### Why the MTR build separates journey durations

MTR connections in this experimental feed represent **complete cached origin/destination journeys**, including internal line changes. They expose just the boarding and alighting stops to OTP. They are not single through trains.

Two paths with the same endpoints and departure time can have different durations. Sharing a GTFS route ID allowed OTP 2.9 to group them into one pattern and hide the faster path. `compile_mtr_interchanges.py` now assigns `:PATH:<duration-seconds>` route IDs while preserving the original public line name, departure bands, service dates, API timings and provenance. Equal-duration paths can still share a pattern. This is part of the normal source build, not a manual ZIP patch.

Synthetic MTR-to-MTR forbidden-transfer rows are omitted: expanding them across the additional patterns exhausted the supported 4 GB OTP heap. The paired Python API still forbids consecutive synthetic MTR legs (`has_split_mtr_journey`) and limits dedicated MTR queries to one whole-journey connection. Other transfer records remain intact. **Use this feed through the paired routing API**; a generic OTP client bypassing that policy can combine synthetic journeys incorrectly.

A separate earlier fix expands MTR's explicit non-peak Tseung Kwan O interchange note into a timetable-valid branch variant. Both fixes apply to all matching source records; neither hard-codes a preferred LOHAS Park–HKU result.

When embedding this backend in another app, deploy the matching feed, OTP graph, build configuration, release manifest and routing adapter together, then restart that app's OTP process. Updating this checkout alone does not update a separately copied backend. Compare `gtfs_sha256` and `graph_sha256` in both release manifests to detect an old copy. Do not copy another app's authentication configuration or database.

## Cache and refresh

The indoor stage retains the original 3D network FGDB, comparison GeoJSON, all
98 station map datasets and their checksums. It preserves vertical lift paths,
exports station map packages and records every withheld connection. Walking
durations are model estimates. See [INDOOR_ROUTING.md](INDOOR_ROUTING.md) for
source matching, build commands, output/API details and limitations.

```sh
# Export raw source files; default output is dist/hk-routing-source-cache.tar.gz.
python3 run.py export-cache

# Import source cache and rebuild. Missing records may be downloaded.
python3 run.py --cache /path/to/hk-routing-source-cache.tar.gz --start-date 2026-09-22

# No network requests during source acquisition/build; dependencies and pinned JARs must exist.
python3 run.py build --offline --start-date 2026-09-22

# Refresh the public sources, including every rail pair. This can take hours.
python3 run.py build --refresh
```

A source cache includes raw API/wiki responses, original government/OSM files and timetable pages, with a SHA-256 manifest. It excludes API credentials, personal coordinate caches, Python environments, compiled graphs, logs, executable JARs and the large duplicate parsed article output. Archive import rejects unexpected paths, links and checksum mismatches. Checksums detect changes; they do not certify the source's real-world accuracy or a third party's identity.

Cache files are deliberately **not tracked by Git**. Only distribute data for which the source terms permit redistribution, with the required attribution; see [DATA_SOURCES.md](../DATA_SOURCES.md). A public cache release is optional, not required to build from source. Do not put a hundreds-of-megabytes archive into Git history.

The collectors cache each successful response. Bus jobs use SQLite progress; MTR jobs use typed station-pair files. Six known co-located Airport Express/Tung Chung Line alias pairs can return no journey; they are recorded rather than turned into invented connections. Other missing/error rail pairs stop the complete build. A cache can contain more evidence than is accepted into GTFS.

## Dates and versions

The default build window is 30 days from today. Change it with `--start-date YYYY-MM-DD --days 30`. The government holiday calendar must cover that period; missing holiday rules are never extrapolated. The UI uses the built graph's date window and defaults departure to the current Hong Kong time. A historical cache may require choosing a historical departure date.

The internal directories named `2026-09-17` and `2026-09-21` are retained storage paths for older parsers. They do **not** identify the current snapshot. `data/source_manifest.json` and individual raw responses carry actual acquisition dates, source URLs and revisions.

OTP **2.9.0** and MobilityData validator **8.0.1** are downloaded from official releases with pinned SHA-256 checksums. Python requirements are pinned in `requirements.txt`. Source websites are external dependencies: if their schema changes or they refuse requests, collection can stop and require a parser update. The fresh path is not guaranteed indefinitely.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `SyntaxError` at a file path | Exit the Python `>>>` prompt; run `python3 run.py` in a terminal. |
| Port already in use | Stop the other instance or use `python3 run.py --port 8002 --otp-port 8083`. |
| Unsupported class version | Install Java 25 and check which `java` is on PATH. |
| Date outside graph/calendar | Pick a date in the displayed window or rebuild with current sources. |
| Site timeout / rate limit | Wait and rerun the same command; cached successful responses are retained. |
| Unsupported timetable layout | Inspect the audit report; do not invent a frequency to force the merge. |
| Java out of memory | Close other heavy processes; allow sufficient free RAM. |
| Offline dependency missing | Run online once, or preinstall `requirements.txt` and the pinned JARs. |

## Project map

| Path | Purpose |
| --- | --- |
| `run.py` | Public setup, resume, cache export/import and server entry point |
| `route_checker/server.py` | Local API, OTP integration and source explanations |
| `route_checker/index.html` | Single-file HTML/CSS/JS checker |
| `scripts/hkbus_pilot/` | Bus/minibus acquisition, matching and timetable merge |
| `scripts/transit_enrichment/` | Official route/stop matching, GMB calendars, historical timing checks and source audit |
| `scripts/hkrail/` | MTR and Light Rail wiki table adapters |
| `scripts/*mtr*.py` | Typed API cache and whole-journey GTFS compilation |
| `data/`, `tools/`, `.venv/` | Ignored local downloads, results and runtime |

The older individual update scripts are retained for research/debugging. New users should use `run.py`; it establishes prerequisites in the correct order.

## Bus/minibus travel times and map paths

The build now estimates missing intermediate stop times using distances along
validated CSDI bus/minibus paths, preserving published timing anchors. It also
adds those paths to GTFS for road-following map display. Unmatched paths retain
an explicitly labelled straight-line-distance fallback. These are timetable
estimates, not live traffic predictions. Source research, limitations, offline
reproduction and the standalone w8g rebuild are in [SURFACE_TIMING.md](SURFACE_TIMING.md).

The final operator stage also compares Sun Ferry CSV and current passenger pages,
then rebuilds the two verified Central–island timetables with vessel classes and
published journey ranges. All 31 discovered KMB section pages are cached and
checked; district predictions are retained as evidence, not invented stop-pair
times. Cache export includes these source files. See [verified changes and
remaining data limits](ROUTING_ACCURACY.md).

The current pipeline also applies [bus/minibus source enrichment](TRANSIT_ENRICHMENT.md) before the final ferry stage. Verified GMB schedules are compiled before CSDI interpolation; accepted historical proportions are applied afterward. All supplied timing anchors remain protected. This stage is included in normal setup, offline rebuilds and cache export.
