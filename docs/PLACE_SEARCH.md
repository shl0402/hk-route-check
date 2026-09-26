# Place search descriptions

Results keep separate OSM and GTFS records, even when names or coordinates overlap.

- **Type:** station, on-track stopping point, bus/minibus stop, restaurant, road segment, etc.
- **Details:** published operator, stop reference, route numbers, unit/floor and address where present.
- **Near …:** a nearby LandsD landmark and its published neighbourhood; approximate straight-line distance, **not** the result’s address or a walking route. Shown only within 600 m.
- **Source and coordinates:** distinguish records where descriptions are otherwise the same. Hover for source record IDs.

The local index rebuilds automatically when its schema or input files change. Inputs are the active GTFS, downloaded OSM extract and optional `data/landsd/places.geojson`; no live search service is required. `Tam Jai` also matches `TamJai`. Existing route results remain visible when search inputs change.
