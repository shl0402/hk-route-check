# LandsD data

`python3 run.py build` now downloads, verifies and merges LandsD data before GTFS validation and the OTP graph build. Use `--offline` with a complete cache, or `--refresh` to request a new snapshot from all sources. Downloads resume from verified files and print station progress.

For the LandsD stage alone:

```sh
.venv/bin/python scripts/landsd_enrich.py fetch
.venv/bin/python scripts/landsd_enrich.py merge
```

The stage writes `data/generated/hk-transit-LANDSD.gtfs.zip`. The complete `run.py build` publishes it as the checker's `hk-transit-EXPERIMENTAL.gtfs.zip` only after validation and graph compilation succeed. Running the standalone merge alone does **not** switch the active checker.

| Source | Output | Policy |
| --- | --- | --- |
| iGeoCom station points | `stops.txt` parent-station coordinates and available addresses | Exact station-name identity, system-aware; never moves a logical platform to the parent coordinate |
| iGeoCom MTR access points | `stops.txt`, entrance/exit type 2 | Explicitly named station access; ambiguous parent stations withheld |
| Indoor MTR floor records | `levels.txt` | Published ordinal/name; no elevation-to-floor guessing. Entrance-to-floor assignments are not guessed |
| Explicit indoor platform points | `stops.txt` with source floor references | Physical reference platforms; original logical boarding stops and trip assignments remain unchanged. Platform numbers copied only when explicit in the source name |
| Chinese source names | Bilingual `stop_name` / `level_name` | Source text, not machine-translated. A feed with existing publisher metadata can use `translations.txt`; no publisher identity is invented for an unpublished local feed |
| Source identity | `attributions.txt`, `landsd_provenance.json` | Source URLs, hashes, timestamps and record IDs |
| Indoor station facilities | `landsd_facilities.geojson` in GTFS ZIP and a sidecar | **Custom extension**, not consumed by OTP; does not create boarding points or pathways |
| General places, buildings, shops | `data/landsd/places.geojson` | Separate search data; not GTFS stops. The existing autocomplete is not switched automatically |
| Five station-map layers | `data/landsd/raw/<venue>/` | Floors, amenities, openings, units and occupants retained verbatim for later use |

No new travel, walking or transfer times are invented. The existing MTR API timing, Light Rail fallback running times, fixed transfer rules and frequency models are preserved. Standard GTFS files unrelated to the additions are copied byte-for-byte by this stage. Reference platforms do not yet change the boarding point of a scheduled trip; that requires verified directional matching.

LandsD indoor `entry` points can be on the concourse side of an exit. They are not automatically street entrances. The current GTFS has logical line boarding points rather than verified physical platform nodes. No nearest-point connections, estimated transfer times, inferred wheelchair access, or incomplete `pathways.txt` are added. OTP may still model access walking; these additions do not turn that into a measured duration.

The existing outdoor/indoor pedestrian downloads are not inputs to this enrichment: station-to-platform links have not been established without inference. Their `AccessTime` tables describe opening hours, not traversal seconds. They cannot replace a five-minute transfer value with a measured duration.

The raw cache is covered by `run.py export-cache` and checksum-checked import. A fresh download is reproducible by procedure but can differ as government data changes. To reproduce an exact snapshot, retain its raw cache and service dates.

## Timing replacement review

| Candidate | Finding | Action |
| --- | --- | --- |
| LandsD indoor/outdoor networks | Geometry, level and direction information; no verified measured traversal times | Preserve current walking/transfer estimates |
| Cached MTR Light Rail all-pairs API | Journey totals and overlapping-path differences; some segment evidence conflicts or is insufficient | Preserve accepted API timings and existing unresolved fallbacks; do not relax evidence thresholds |
| MTR Light Rail Next Train API | Live predictions, not a historical/static trip timetable | Do not freeze a live ETA into recurring GTFS departures |
| TD routes/fares data | Route-level `journeyTime`, not every intermediate stop time | Do not divide the total among stops and call the result sourced timing |
| Wiki frequency ranges | Published intervals, not exact individual departures | Preserve ranges in provenance and existing frequency policy |

References: [LandsD indoor API](https://portal.csdi.gov.hk/csdi-webpage/apidoc/3d-indoor-mtr-station-map), [iGeoCom](https://www.landsd.gov.hk/en/survey-mapping/mapping/other-products/iGeoCom.html), [GTFS reference](https://gtfs.org/documentation/schedule/reference/), [Light Rail API dictionary](https://opendata.mtr.com.hk/doc/LR_Next_Train_DataDictionary_v1.0.pdf), [TD route-data specification](https://static.data.gov.hk/td/routes-fares-geojson/dataspec/ptroutefare_geojson_dataspec.pdf).
