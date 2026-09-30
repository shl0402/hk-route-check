# Routing corrections · 30 September 2026

| Change | Result |
|---|---|
| MTR station alternatives | Nearby boarding/exit stations are queried explicitly; no one-route-per-mode quota. |
| Fastest | Arrival minus requested departure, including every visible wait. A shorter trip leaving later no longer wins by duration alone. |
| Sparse ferry services | Empty searches expand from 1 to 2 to 4 hours. |
| Sun Ferry | 277 trip templates: 203 fast, 74 ordinary; current departures, day rules and published journey ranges. |
| Mui Wo stale CSV | Current timetable selected; 48 day/departure entries differ in each direction of comparison and 126 day/departure entries disagree on vessel class. These are expanded day-type records, not 126 individual sailings. |
| KMB public section predictions | 31 operator pages, 2,254 records, 2,227 numeric predictions. Checked service links identify an origin stop and destination area, but no exact destination stop or prediction validity interval. **0 installed as exact stop-pair times.** |
| Map access gaps | Endpoint gaps above 50 m are shown explicitly; missing access time is not fabricated. |

## Tested on the rebuilt graph

**127 source tests and 15 checker tests passed. GTFS validation: 0 errors. Eight additional live scenarios passed**: MTR alternatives, less walking, three ferry routes, bus/light rail, rural access and walking only. These check routing behaviour and source use, not measured real-world arrival accuracy.

The final running server also passed both earlier MTR regressions: Mong Kok → North Point keeps the complete 22-minute operator journey; Saturday LOHAS Park → HKU retains the 37-minute journey via Tseung Kwan O and North Point.

LOHAS Park eastern street (`22.2973, 114.272`) → 南洋中心 (`22.29976928, 114.17862282`), **30 September, 17:00 HKT**:

| Result | Total from 17:00 | Walk |
|---|---:|---:|
| MTR → Tsim Sha Tsui | 63.3 min | 1,553 m |
| Bus 790 → 796X → 8 | 63.7 min | 637 m |
| KMB bus 49 | 65.3 min | 1,286 m |
| MTR → Hung Hom | 67.3 min | 1,410 m |
| MTR → Jordan | 69.1 min | 1,843 m |
| MTR → East Tsim Sha Tsui | 69.3 min | 1,292 m |

The separate MTR-only **less walking** search ranked East Tsim Sha Tsui first. Searches took 32.5 and 33.4 seconds; one station query reached its timeout in the latter, and the response disclosed this. No global top-six guarantee is claimed.

Cheung Chau and Mui Wo used the operator's 40-minute fast-ferry allowance. Sok Kwu Wan required the expanded two-hour departure search. Cheung Chau, Sok Kwu Wan and the rural test exposed missing endpoint connections; their displayed times explicitly exclude those unverified connections. Saved requests/responses: `data/routing-checks/2026-09-30/`.

## Verification decisions

The [current Mui Wo timetable](https://www.sunferry.com.hk/en/route-and-fare/timetable?route=central-to-mui-wo) and [operator notice effective 10 August 2026](https://www.sunferry.com.hk/en/sun-ferry/news-update/new-sailing-schedule-forcentral-mui-wo-route/265) supersede the conflicting CSV. The linked current PDF is archived too. The 03:00 Central departure uses Pier 5 as stated, not the route's usual Pier 6.

[Cheung Chau](https://www.sunferry.com.hk/en/route-and-fare/timetable?route=central-to-cheung-chau) gives fast-ferry and ordinary-ferry ranges. The feed uses their upper bounds, 40 and 60 minutes, and marks arrivals approximate. Four ordinary trip templates have a conditional fast-vessel notice; they retain the ordinary planning allowance because the operator may revert without notice. The notice is included in their provenance.

[KMB section pages](https://app.kmb.hk/app1933/BBI/bbi_stop.php?id=0019) have been checked, including their decoded route-detail links. District predictions cannot be attached to a guessed destination stop or treated as an all-day timetable. Raw pages and normalized records remain available for comparison. They are not a user review task.

## Reproduce

```sh
python3 run.py build --offline --start-date 2026-09-17 --days 30
python3 run.py check
python3 run.py
# In another terminal, while the checker is running:
python3 route_checker/check_routing_regressions.py --date 2026-09-30
```

Omit `--offline` to fetch missing sources. `run.py export-cache` includes operator raw responses and checksums. To refresh only these small operator sources, run `python3 scripts/operator_sources.py fetch --refresh`, then rebuild. A full `run.py build --refresh` also recollects the much larger rail/wiki sources.

`operator_sources.py` reparses cached pages and compares CSV with HTML. `operator_timing.py` replaces only the two identified ferry routes, validates TD holiday calendars and existing overnight service-day offsets, and proves unrelated trips/times are unchanged. Unknown layouts/identities fail the build. The new feed is independently validated and its OTP graph rebuilt before publication.

Evidence: `data/operators/raw/manifest.json`, `normalized.json`, `report.json`, and `operator_timing_provenance.json` inside the GTFS. Sources are downloaded snapshots, not live arrival data.

## Remaining data limits

- Bus/minibus intermediate times still use published timing anchors plus path-distance interpolation. Official CSDI geometry already covers 3,079 matched patterns; unmatched paths retain explicit fallback labels. No complete measured future stop-pair timing table was verified.
- MTR whole-journey values are operator estimates. Their hidden waiting allowance is not separately identified; the visible first boarding wait remains a separate model value. OTP 2.9 frequency routing uses a full interval for `exact_times=0`; these are not live predictions.
- Walking and lift timings remain models. Published indoor geometry does not supply measured walking times; outdoor LandsD paths cannot be merged by proximity without checking connectivity and access restrictions.
- Rail/tram/ferry map paths without verified GTFS shapes can still join known stops. No invented track alignment is presented as source geometry.
- Candidate enumeration is bounded. Results are the best distinct routes found, not a mathematical proof of the global top six.
