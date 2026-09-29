# Indoor routing validation — 29 September 2026, revision 2

This records the September source snapshot, not live arrivals or measured indoor
walking times. See [INDOOR_ROUTING.md](INDOOR_ROUTING.md) for reproducible builds,
matching safeguards and the five unresolved stations with source references.

## Result

Coverage increased from 11 to **93 of 98 stations**: **46 complete**, **47 partial**,
and **5 withheld**. Complete means all entrance/platform candidates evaluated by
this importer passed; it does not guarantee that the public datasets include every
real entrance. Partial stations publish only their verified connections.

The feed contains **16,358 directed/bidirectional pathway records**, connecting
**507 independently matched entrances** and **162 physical platform areas**.
**1,595,010 MTR stop calls** use verified physical platforms; **65,478** retain their
legacy logical boarding locations because the current evidence is insufficient.
All 98 maps remain available for reference.

The main repairs are directional platform assignment from cached MTR evidence,
shared physical station identity across lines, ground-level entrance selection,
source name normalization, partial station activation, continuous lift handling,
and tightly bounded coordinate precision normalization. No missing corridor is
filled with an arbitrary straight line.

## Cross-validation

- All 98 maps and five interior layers; all 55,542 original network segments.
- Original FGDB compared with companion GeoJSON by source identifiers.
- Eight lift paths collapsed to points in GeoJSON use the original 3D lines.
- Twenty-one attribute disagreements and 1,195 floor-ID differences are recorded;
  published numeric floor IDs join aliases. Original FGDB remains authoritative.
- Every active platform has an entrance reachable in both directions, checked
  independently from the resulting GTFS edges, not merely compiler summaries.
- All **3,585,456 stop-time rows** preserve every field except verified MTR stop IDs.
  Ten other GTFS tables, including trips, frequencies and transfers, are unchanged.
- **93 source tests pass**, including wrong-floor prevention, access restrictions,
  conflicting platform evidence, lift segmentation and release rollback/integrity.
- Independent GTFS validator: **zero ERROR notices and zero system errors**.
- Complete offline source build and OTP graph compilation pass.
- Independent embedded-style rebuild: all **14 GTFS text tables** are byte-identical;
  station index and validation report match (excluding the input ZIP-container hash).
  Its independent validator and OTP graph compilation also pass. ZIP/graph container
  hashes need not match because serialization metadata can differ.
- Three live entrance regressions and seven MTR regressions pass, including weekday,
  Saturday, reverse travel, multiple stations and bus alternatives.
- Six additional live checks confirm Hang Hau platform 1/2 in both directions,
  and Tsing Yi/Hong Kong Tung Chung Line platform assignments without confusing
  the shared Airport Express station identity.

| Published entrance test | LOHAS access distance | Access time | Total to/from HKU |
|---|---:|---:|---:|
| LOHAS C1 → HKU | 168 m | 4.9 min | 55.3 min |
| LOHAS C2 → HKU | 180 m | 5.1 min | 55.5 min |
| HKU → LOHAS C1 | 168 m | 4.9 min | 59.4 min |

Tests depart 29 September 2026 at 11:00 HKT. The cached MTR portion remains
37 minutes. Totals differ from revision 1 because HKU now includes modeled indoor
access instead of the old logical boarding point. These are estimates, not a claim
that the app exactly matches Google's walking or waiting-time model.

## Repeat the checks

Against an already-running matching graph (choose its actual OTP port):

```sh
.venv/bin/python route_checker/check_indoor_routes.py --otp-port 8081 --directions
.venv/bin/python route_checker/check_mtr_routes.py --otp-port 8081
```

Timetable preservation and topology without a running server, in map_routing:

```sh
.venv/bin/python route_checker/check_indoor_routes.py --feed-only --base data/build-work/data/generated/hk-transit-LANDSD.gtfs.zip
.venv/bin/python scripts/check_source_tests.py
```

In the native w8g directory, the bundled base is instead
`data/indoor-source/hk-transit-LANDSD.gtfs.zip`; use that with `--base`. The bundled
`scripts/rebuild_indoor.py` reproduces an isolated candidate from its own sources,
without depending on map_routing or app_prototype. Start/stop the app server yourself.

Existing saved route snapshots do not recalculate automatically: make a new search
after restarting. Drawing indoor floor layers in Swift remains a separate UI task.

## Station coverage

The machine-readable report also lists every excluded entrance/platform, raw source
identifier, coordinate mismatch and accepted path. It is generated on every rebuild
at `data/landsd/indoor/validation.json` and embedded as `indoor_provenance.json`.

| Station | Coverage | Verified entrances | Physical platform areas |
|---|---|---:|---:|
| Admiralty Station | Complete | 7 | 6 |
| Airport Station | Withheld | 0 | 0 |
| AsiaWorld-Expo Station | Complete | 2 | 1 |
| Austin Station | Partial | 10 | 1 |
| Causeway Bay Station | Complete | 11 | 2 |
| Central Station | Partial | 12 | 3 |
| Chai Wan Station | Partial | 4 | 1 |
| Che Kung Temple Station | Complete | 6 | 2 |
| Cheung Sha Wan Station | Complete | 7 | 1 |
| Choi Hung Station | Partial | 4 | 2 |
| City One Station | Complete | 4 | 1 |
| Diamond Hill Station | Partial | 5 | 2 |
| Disneyland Resort Station | Complete | 1 | 1 |
| East Tsim Sha Tsui Station | Partial | 15 | 1 |
| Exhibition Centre Station | Complete | 6 | 2 |
| Fanling Station | Complete | 5 | 2 |
| Fo Tan Station | Complete | 4 | 2 |
| Fortress Hill Station | Withheld | 0 | 0 |
| HKU Station | Complete | 6 | 1 |
| Hang Hau Station | Complete | 4 | 2 |
| Heng Fa Chuen Station | Complete | 2 | 2 |
| Heng On Station | Complete | 3 | 1 |
| Hin Keng Station | Complete | 1 | 2 |
| Ho Man Tin Station | Complete | 7 | 3 |
| Hong Kong Station | Partial | 6 | 2 |
| Hung Hom Station | Partial | 13 | 2 |
| Jordan Station | Partial | 4 | 1 |
| Kai Tak Station | Partial | 4 | 1 |
| Kam Sheung Road Station | Complete | 4 | 1 |
| Kennedy Town Station | Partial | 3 | 1 |
| Kowloon Bay Station | Partial | 2 | 1 |
| Kowloon Station | Partial | 9 | 2 |
| Kowloon Tong Station | Partial | 7 | 3 |
| Kwai Fong Station | Complete | 5 | 2 |
| Kwai Hing Station | Complete | 5 | 2 |
| Kwun Tong Station | Complete | 16 | 1 |
| LOHAS Park Station | Complete | 3 | 1 |
| Lai Chi Kok Station | Partial | 4 | 1 |
| Lai King Station | Partial | 4 | 4 |
| Lam Tin Station | Complete | 5 | 1 |
| Lei Tung Station | Partial | 2 | 1 |
| Lo Wu Station | Withheld | 0 | 0 |
| Lok Fu Station | Complete | 2 | 1 |
| Lok Ma Chau Station | Withheld | 0 | 0 |
| Long Ping Station | Partial | 5 | 1 |
| Ma On Shan Station | Complete | 3 | 1 |
| Mei Foo Station | Partial | 7 | 3 |
| Mong Kok East Station | Partial | 3 | 2 |
| Mong Kok Station | Partial | 14 | 3 |
| Nam Cheong Station | Complete | 6 | 4 |
| Ngau Tau Kok Station | Complete | 8 | 1 |
| North Point Station | Partial | 5 | 2 |
| Ocean Park Station | Partial | 2 | 2 |
| Olympic Station | Complete | 12 | 2 |
| Po Lam Station | Complete | 6 | 1 |
| Prince Edward Station | Partial | 6 | 4 |
| Quarry Bay Station | Partial | 2 | 3 |
| Racecourse Station | Withheld | 0 | 0 |
| Sai Wan Ho Station | Complete | 2 | 2 |
| Sai Ying Pun Station | Complete | 6 | 1 |
| Sha Tin Station | Partial | 4 | 2 |
| Sha Tin Wai Station | Complete | 4 | 1 |
| Sham Shui Po Station | Partial | 6 | 1 |
| Shau Kei Wan Station | Partial | 8 | 1 |
| Shek Kip Mei Station | Partial | 2 | 1 |
| Shek Mun Station | Complete | 4 | 1 |
| Sheung Shui Station | Partial | 9 | 2 |
| Sheung Wan Station | Complete | 10 | 2 |
| Siu Hong Station | Partial | 4 | 1 |
| South Horizons Station | Complete | 3 | 1 |
| Sung Wong Toi Station | Partial | 4 | 1 |
| Sunny Bay Station | Complete | 1 | 3 |
| Tai Koo Station | Partial | 7 | 1 |
| Tai Po Market Station | Complete | 4 | 2 |
| Tai Shui Hang Station | Complete | 2 | 1 |
| Tai Wai Station | Partial | 7 | 4 |
| Tai Wo Hau Station | Complete | 3 | 1 |
| Tai Wo Station | Partial | 1 | 2 |
| Tin Hau Station | Complete | 3 | 2 |
| Tin Shui Wai Station | Complete | 7 | 1 |
| Tiu Keng Leng Station | Partial | 3 | 3 |
| To Kwa Wan Station | Partial | 3 | 2 |
| Tseung Kwan O Station | Complete | 5 | 1 |
| Tsim Sha Tsui Station | Complete | 12 | 1 |
| Tsing Yi Station | Partial | 1 | 4 |
| Tsuen Wan Station | Partial | 8 | 1 |
| Tsuen Wan West Station | Partial | 11 | 2 |
| Tuen Mun Station | Complete | 9 | 1 |
| Tung Chung Station | Complete | 4 | 1 |
| University Station | Complete | 4 | 2 |
| Wan Chai Station | Partial | 6 | 2 |
| Whampoa Station | Partial | 6 | 1 |
| Wong Chuk Hang Station | Partial | 3 | 1 |
| Wong Tai Sin Station | Partial | 9 | 1 |
| Wu Kai Sha Station | Complete | 3 | 1 |
| Yau Ma Tei Station | Partial | 5 | 2 |
| Yau Tong Station | Partial | 2 | 3 |
| Yuen Long Station | Partial | 9 | 1 |
