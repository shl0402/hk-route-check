# Bus and minibus source enrichment

The normal `run.py build` now collects and verifies operator route identities,
stop coordinates, official minibus timetables, public holidays and historical
ETA-derived timing data. The paired server reads provenance from the **active
GTFS**, so a later download cannot silently relabel an older graph.

## Applied snapshot · 3 October 2026

| Result | Count |
|---|---:|
| Official cached source files | 9,930 |
| Verified GTFS bus/minibus stop patterns | 2,457 / 3,511 |
| Native operator stops verified against the feed | 12,625 |
| GMB direction/service patterns with official timetables applied | 1,118 |
| Matched GMB patterns retaining old timetable due to source conflicts | 30 |
| New timetable templates: frequency bands / listed departures | 4,563 / 65 |
| Validated road shapes, including repeated-road circular routes | 3,261 (+182) |
| Patterns receiving checked historical timing proportions | 985 |
| Intermediate stop rows changed | 375,745 |
| Supplied arrival/departure fields preserved | 3,769,306 |
| Historical hour/day profiles collected for research | 168 |

Counts of templates/rows include repeated calendar variants; they are not counts
of physical buses. GTFS validation: **zero errors**. Sources rejected for
ambiguity remain cached; their previous feed values are retained.

## Rebuild

```sh
python3 run.py build
python3 run.py check
python3 run.py
```

Existing snapshots can be rebuilt without network access:

```sh
python3 run.py build --offline --start-date 2026-09-17 --days 30
```

To refresh only these sources, without repeating the rail/wiki collection:

```sh
.venv/bin/python scripts/enrich_transit.py fetch --refresh
python3 run.py build --offline --start-date 2026-09-17 --days 30
```

Successful requests are cached, hashed and resumed. `python3 run.py status`
shows collection/build progress. `run.py export-cache` includes these raw
responses and inert source-audit snapshots; no source-audit code is executed.
A refresh stops if the upstream historical estimator changes until its new
method is checked. A source cache reproduces the same evidence and decisions;
GTFS ZIP timestamps need not be byte-identical between fresh builds.

## What is applied

| Data | Acceptance rule | Use |
|---|---|---|
| KMB, Citybus and GMB stop sequences | Same operator/route, entire ordered sequence, coordinate checks; preserve repeated visits and service variants | Verified service identity, native coordinates and live-arrival lookup |
| Citybus circular routes | Explicit circular route, exact overlapping official halves and full merged stop sequence | Correct bound and native stop occurrence for each visit |
| GMB timetable | Complete interpretable day rules, government holiday coverage, no unresolved overlaps or special-day conditions | Replace matched timetable within the requested build dates |
| Published headway range | Retain both limits; use the upper limit with `exact_times=0` | Conservative planning interval, **not exact departures** |
| TD/CSDI route paths | Full ordered-stop match; preserve exactly connected source sections, including repeated roads | Road-following GTFS shapes and map display |
| Historical ETA differences | Unique matched service, complete span, validated path, no known conflicting paths, plausible speed and inferred total within ⅔–1.5 of the published span | Reweight **missing intermediate times only**, preserving every published timing anchor and total |
| Official live arrivals | Exact verified route, direction, variant and stop occurrence; fresh response | Optional “Live arrivals” in the checker; never written into GTFS |

Stop-pair historical values have no route ID, vehicle ID, sample count or
per-pair observation date. The upstream method matches predictions, adds a 10%
margin and smooths results. They remain **experimental estimates**, not measured
journey times. Shared-pair geometry checks cover matched feed routes; unknown
routes can still contaminate upstream values. All 168 hour/day profiles are
retained for research; the static feed uses only accepted all-day proportions.
Missing or conflicting spans keep the prior distance interpolation.

Native stop coordinates remain available as evidence. Shared government stops
are not automatically moved to one operator's boarding bay. Unverified shapes
for rail, Light Rail and ferries are not imported from third-party map drawings.
MTR whole-journey times and existing verified N796 departures are preserved.

## Evidence and checks

- `data/transit_enrichment/raw/`: original responses, immutable Git revisions,
  licenses, timestamps and checksums.
- `data/transit_enrichment/report.json`: applied counts and rejection reasons.
- `data/transit_enrichment/base_validation.json` and `validation.json`:
  independent preservation checks.
- `transit_enrichment_provenance.json` inside the GTFS: per-trip timetable,
  identity and running-time sources. Unknown extension files are ignored by
  generic GTFS consumers.
- `GET /api/status`: active enrichment counts.
- `GET /api/transit/arrivals?trip_id=…&match=0&stop_id=…&sequence=…`:
  official arrivals **now**, selected only through the active feed's verified
  identities. Arbitrary source URLs and unverified stops are rejected. The
  response distinguishes scheduled times from predictions; the plan is unchanged.

The build checks all original stop visits/timing anchors, unrelated trips,
calendar exclusions, overnight rules and GTFS validity before publishing the
new graph. Source accuracy and real traffic remain separate from format validity.

The final offline rebuild passes **221 source tests** and the independent GTFS
validator with **zero errors**. Geometry review found 26 closest-point warnings
on repeated 95C loops and five pre-existing close-stop warnings near Po Lam.
Explicit stop distances stay ordered; shared stop coordinates do not identify
every terminal bay. Other retained source warnings, such as duplicate public
route names, are listed in the validator report.

Eight live routing regressions passed on the final graph: MTR station
alternatives, three ferry journeys, Light Rail/bus, rural access and walking.
N796 departure checks and 74X historical-source checks also passed; the browser
verified 112M's official headway and scheduled live-arrival labels. Evidence is
in `data/routing-checks/2026-10-03/`. One MTR alternative query was unavailable;
completed candidates were returned. Existing ferry/rural access-gap warnings
remain visible. These checks prove pipeline behaviour, not measured traffic
accuracy or that every possible route was found.

## Known source conflicts

N796's government estimate is **69 minutes for the full loop**. The historical
ETA-derived total is **126.2 minutes**; 03:00 profiles give approximately
**104–109 minutes**. The current [TD route XML](https://static.data.gov.hk/td/routes-fares-xml/ROUTE_BUS.xml)
still publishes 69. The [Citybus timetable](https://www.citybus.com.hk/en/uploadedFiles/cust_notice/TS-NWFB-N796-N796-N.pdf)
does not provide a replacement running time. The pipeline therefore rejects
this historical retiming; it does not certify the retained estimate as an
actual measured journey.

Official GMB rows also contain apparent overnight/AM–PM and headway-range
errors. Unsupported records keep the previous timetable; no values are silently
flipped or invented.
