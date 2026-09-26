# Sources and attribution

The MIT license covers this project's code. **It does not relicense downloaded data, wiki text/images, map tiles, or third-party software.** Raw responses and revisions are retained locally; GTFS provenance extensions and the checker link back to timing sources.

| Source | Used for | Terms / credit |
| --- | --- | --- |
| [Hong Kong Transport Department GTFS](https://static.data.gov.hk/td/pt-headway-en/gtfs.zip) and [route metadata](https://data.gov.hk/en/) | Original services, stops, calendars, bus running-time offsets and identity checks | HKSAR Government / Transport Department; [DATA.GOV.HK terms](https://data.gov.hk/en/terms-and-conditions) |
| [MTR Open Data](https://opendata.mtr.com.hk/) | Heavy rail / Light Rail station inventories | MTR Corporation; source's applicable terms |
| [MTR journey planner](https://www.mtr.com.hk/en/customer/jp/index.php) and [service information](https://www.mtr.com.hk/en/customer/services/train_service_index.html) | Coordinates, all-pairs journey estimates, service hours and published intervals | MTR Corporation; website/API content is separate from the code license. Public access does not itself establish redistribution rights. |
| [Hong Kong Bus Wiki](https://hkbus.fandom.com/wiki/巴士路線) | Timetables, departures, route variants and supporting evidence | Wiki contributors; retain article/revision attribution and check the community footer/license before redistribution. |
| [Hong Kong Railway Wiki](https://hkrail.fandom.com/wiki/港鐵) | MTR and Light Rail published timetable tables | Wiki contributors; same source-specific license/attribution requirements. |
| [OpenStreetMap](https://www.openstreetmap.org/copyright), via [Geofabrik](https://download.geofabrik.de/asia/china/hong-kong.html) | Walking graph, local place search and station/entrance features | © OpenStreetMap contributors, ODbL; source map/tiles retain their attribution. |
| [Lands Department iGeoCom](https://www.landsd.gov.hk/en/survey-mapping/mapping/other-products/iGeoCom.html) and [3D Indoor MTR Station Map](https://portal.csdi.gov.hk/csdi-webpage/apidoc/3d-indoor-mtr-station-map) | Published station coordinates, named railway access points, floor records, supplemental facilities and local POI data | HKSAR Government / Lands Department; source data is not MIT-licensed. Source URLs, retrieval times, checksums and available feature revision dates are retained. |

LandsD additions are rebuilt by `scripts/landsd_enrich.py`, automatically run by `run.py`. No walking durations, wheelchair guarantees, platform connections or entrance-to-floor matches are inferred. Indoor facilities in `landsd_facilities.geojson` inside the ZIP are a documented extension, not standard GTFS pathways. General POIs stay in `data/landsd/places.geojson`; they are not inserted as transit stops. Existing timing estimates remain where no verified replacement exists. See [LandsD data details](docs/LANDSD.md).

Wiki content licensing can vary, and images can have different terms from text: [Fandom reuse guidance](https://support.fandom.com/hc/en-us/articles/360035075654-I-want-to-reuse-text-or-images-from-a-Fandom-wiki). The project stores source image links as evidence; it does not download and relicense wiki illustrations.

The optional cache export is a local reproducibility tool. Before hosting an archive publicly, establish permission for the included MTR website/API responses and other source material, and include the source licenses/attributions. The code-only repository works without a published cache. Do not assume an exported archive is automatically cleared for redistribution.

Runtime components: [OpenTripPlanner](https://github.com/opentripplanner/OpenTripPlanner) (LGPL), [MobilityData GTFS Validator](https://github.com/MobilityData/gtfs-validator) (Apache-2.0), [Leaflet](https://leafletjs.com/) (BSD-2-Clause), and Python packages under their respective licenses. Their binaries are fetched separately, not included in the MIT source distribution.

No endorsement by transport operators, government agencies, map contributors or wiki communities is implied. This is a local research checker; source timings may be rounded, historical or incomplete.
