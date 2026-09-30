# Bus operator identity and app colours

The original GTFS already separates operators through `routes.agency_id` and
`agency.txt`. Bus and green minibus services both use standard GTFS route type 3;
keep this intact for OTP compatibility. No split ZIPs or graph rebuild is needed.

`route_checker/transit_identity.py` reads the active feed and adds optional fields
to each bus leg's `route` in the normalized API result:

- `agencyId`: original GTFS agency ID.
- `operatorIds`: all operators (e.g. `["KMB", "CTB"]`).
- `operatorName`: human-readable operator label.
- `serviceKind`: `bus` or `minibus`.
- `displayColor`, `badgeColor`, `textColor`: six-digit RGB hex without `#`.

The leg retains its OTP `BUS` mode, so existing transport filters still work.
`transportGroup` and `transportLabel` also carry service kind and provider label.
No operator is guessed from the route number. Unknown route IDs remain unchanged.
The metadata cache refreshes when the active feed's timestamp/size changes.

| Provider | App colour |
| --- | --- |
| KMB | Red |
| Citybus | Golden yellow |
| Green minibus | Soft sage green |
| Long Win | Orange |
| Joint services, including 690S | Amber, with both operator names |
| Other operators | Slate blue, with the supplied operator name |

These are app styling choices, not guarantees of a particular vehicle's livery.
Joint-service data does not identify which company's vehicle operates a trip.
Minibus operator businesses are not individually identified by the source's
aggregate GMB agency. Rail, walking, tram and ferry colours remain unchanged.

Swift decodes optional fields in `JourneyLeg.TransitLine`. `TransitAppearance`
uses the supplied colour for map lines and separate readable badge text and
background colours. Journey details and VoiceOver expose operator names.
Old saved routes still decode; without provider metadata they keep legacy
fallback styling. Search and save a new route to capture the new metadata offline.

Both map_routing and the standalone w8g server ship the identity module. The
routing release packager includes it for future installs. Restart the API server
to load new Python code; no feed/graph changes or OTP restart are needed for this
feature. Rebuild the Swift app to load the new UI.

Verification: `python3 scripts/check_source_tests.py` includes synthetic tests for
provider identity, joint services, unchanged non-bus legs, and badge contrast.
