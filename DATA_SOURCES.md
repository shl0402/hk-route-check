# Sources and attribution

The MIT license covers this project's code. **It does not relicense downloaded data, wiki text/images, map tiles, or third-party software.** Raw responses and revisions are retained locally; GTFS provenance extensions and the checker link back to timing sources.

| Source | Used for | Terms / credit |
| --- | --- | --- |
| [Hong Kong Transport Department GTFS](https://static.data.gov.hk/td/pt-headway-en/gtfs.zip) and [route metadata](https://data.gov.hk/en/) | Original services, stops, calendars, bus running-time offsets and identity checks | HKSAR Government / Transport Department; [DATA.GOV.HK terms](https://data.gov.hk/en/terms-and-conditions) |
| [Sun Ferry passenger timetables](https://www.sunferry.com.hk/en/route-and-fare/timetable?route=central-to-mui-wo) | Central–Mui Wo and Central–Cheung Chau departures, vessel classes and operator journey ranges | Sun Ferry Services Company Limited; source terms apply. HTML, CSV, linked PDFs, notice and hashes are retained separately from the code license. |
| [KMB interchange information](https://app.kmb.hk/app1933/BBI/bbi_stop.php?id=0019) | District-level section predictions retained as reference evidence; not installed as exact stop-pair times | The Kowloon Motor Bus Company (1933) Limited; source terms apply. |
| [MTR Open Data](https://opendata.mtr.com.hk/) | Heavy rail / Light Rail station inventories | MTR Corporation; source's applicable terms |
| [MTR journey planner](https://www.mtr.com.hk/en/customer/jp/index.php) and [service information](https://www.mtr.com.hk/en/customer/services/train_service_index.html) | Coordinates, all-pairs journey estimates, service hours and published intervals | MTR Corporation; website/API content is separate from the code license. Public access does not itself establish redistribution rights. |
| [Hong Kong Bus Wiki](https://hkbus.fandom.com/wiki/巴士路線) | Timetables, departures, route variants and supporting evidence | Wiki contributors; retain article/revision attribution and check the community footer/license before redistribution. |
| [Hong Kong Railway Wiki](https://hkrail.fandom.com/wiki/港鐵) | MTR and Light Rail published timetable tables | Wiki contributors; same source-specific license/attribution requirements. |
| [OpenStreetMap](https://www.openstreetmap.org/copyright), via [Geofabrik](https://download.geofabrik.de/asia/china/hong-kong.html) | Walking graph, local place search and station/entrance features | © OpenStreetMap contributors, ODbL; source map/tiles retain their attribution. |
| [Lands Department iGeoCom](https://www.landsd.gov.hk/en/survey-mapping/mapping/other-products/iGeoCom.html) and [3D Indoor MTR Station Map](https://portal.csdi.gov.hk/csdi-webpage/apidoc/3d-indoor-mtr-station-map) | Published station coordinates, named railway access points, floor records, supplemental facilities and local POI data | HKSAR Government / Lands Department; source data is not MIT-licensed. Source URLs, retrieval times, checksums and available feature revision dates are retained. |

The [LandsD 3D Indoor Network](https://data.gov.hk/en-data/dataset/hk-landsd-openmap-3d-indoor-network) supplies source-backed paths, heights, directions, access restrictions and relational tables. Credit: Lands Department, HKSAR Government. The source FGDB and comparison GeoJSON are retained with acquisition metadata and SHA-256 checksums; these data are not MIT-licensed.

LandsD additions are rebuilt by `scripts/landsd_enrich.py` and `scripts/indoor_network.py`, automatically run by `run.py`. Validated stations receive standard GTFS pathways; ambiguous stations keep prior routing. Indoor walking times are explicitly labelled model estimates, not LandsD measurements. No wheelchair guarantee or directional boarding-platform assignment is claimed. Complete indoor map layers remain available as supplemental data. General POIs remain in `data/landsd/places.geojson`, not transit stops. See [indoor build and validation](docs/INDOOR_ROUTING.md).

Wiki content licensing can vary, and images can have different terms from text: [Fandom reuse guidance](https://support.fandom.com/hc/en-us/articles/360035075654-I-want-to-reuse-text-or-images-from-a-Fandom-wiki). The project stores source image links as evidence; it does not download and relicense wiki illustrations.

The optional cache export is a local reproducibility tool. Before hosting an archive publicly, establish permission for the included MTR website/API responses and other source material, and include the source licenses/attributions. The code-only repository works without a published cache. Do not assume an exported archive is automatically cleared for redistribution.

Runtime components: [OpenTripPlanner](https://github.com/opentripplanner/OpenTripPlanner) (LGPL), [MobilityData GTFS Validator](https://github.com/MobilityData/gtfs-validator) (Apache-2.0), [Leaflet](https://leafletjs.com/) (BSD-2-Clause), and Python packages under their respective licenses. Their binaries are fetched separately, not included in the MIT source distribution.

No endorsement by transport operators, government agencies, map contributors or wiki communities is implied. This is a local research checker; source timings may be rounded, historical or incomplete.
