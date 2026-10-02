# Two-point route API

Start the app with `python3 run.py`, then send JSON to `POST /api/route`:

```json
{
  "origin": {"lat": 22.29559, "lon": 114.26873},
  "destination": {"lat": 22.2839758, "lon": 114.1355067},
  "departure": "2026-09-26T07:32",
  "modes": ["mtr", "bus", "ferry", "light_rail", "tram", "funicular"],
  "preference": "fastest",
  "includeAlternatives": true,
  "maxResults": 6
}
```

Use a departure date within your built GTFS calendar. Preferences: `fastest`, `transfers`, `walking`.

## MTR line sections

An MTR `legs[]` entry may represent a complete operator-timed journey, including
interchanges. For display, use its optional `railSections[]` instead of drawing
the whole connection in its first line's colour. Each section has its own route
name/colour, endpoints, `stopCalls`, `apiPath`, start/end and duration. Adjacent
sections share the interchange station. Same-line train changes also split.

Section offsets come from cached MTR cumulative path times; their durations sum
to the parent leg duration and already include internal interchange time. Do not
add another wait or sum parent and child durations. Routing costs, parent
`internalTransfers` and the timetable are unchanged. The native app uses sections
for the flow, stop timeline and map, and can reconstruct older saved sections
from retained line suffixes and timed interchange instructions.

This is an API/presentation change: restart the server after updating the Python
files; no feed/graph rebuild is required. Reproduce the regression with
`python3 -m unittest discover -s tests -p test_mtr_sections.py -v`.

## Alternatives

1. Search the selected transport. A separate MTR search preserves complete station-pair journeys.
2. When enabled, additionally search the selected non-MTR transport and bus/minibus alone, where applicable. No unchecked mode is added.
3. Recover MTR boarding/alighting alternatives: up to six nearby physical stations per end within 2.5 km, at most 16 constrained searches (up to 32 OTP calls including departure-minute checks) within a 30-second budget. OTP calculates the full walking and transit journey; straight-line proximity is only used to discover stations. Check that the returned route actually boards/alights at the requested station.
4. Combine valid results, remove repeated paths, and sort **all** results by the chosen preference. Return up to `maxResults` (1–10; default 6). Different MTR stations and internal paths survive; later copies of the same path do not. No mode has a reserved slot.

Slower alternatives can survive because each transport subset is searched separately. More candidates in one OTP search alone would not recover routes that OTP excludes as worse. Six is a limit, not a guarantee of six results or one of every mode.

`includeAlternatives: false` skips the extra subset searches. An unavailable optional search is reported in `warnings`; completed results remain usable. Multi-stop optimisation skips these extra searches automatically.

The response includes `itineraries`, `searches`, `warnings`, `elapsedSeconds`, and source explanations. `fastest` sorts by **arrival time**; `transfers` sorts by transfer count then arrival; `walking` sorts by walking distance then arrival. Fastest-search walking reluctance is 1 and boarding preference cost is 0; actual walking, waits and boarding buffers remain.

`displayDurationSeconds` / `elapsedFromRequestSeconds` measure arrival minus the requested departure. `duration` measures the itinerary after leaving. `originWaitSeconds` is the time before leaving; `initialWaitSeconds` is the first boarding gap; both count. `initialWaitExcludedSeconds` is retained as 0 for compatibility.

A result without a usable public-transport journey retries with 2-hour and then 4-hour search windows, retaining any walking option. This is a bounded search, not a guarantee of finding every feasible route. Worker optimisation skips the extra alternatives.

`accessGaps` reports requested endpoints more than 50 m from the returned map geometry. The UI warns that access is unverified and missing from the time; no straight-line connector or invented walking time is inserted. Absence of that warning does not certify accessibility or map completeness.

## Frequency waits and missed early departures

GTFS `exact_times=0` describes frequency service, not a known train departure.
Do not subtract the first boarding gap or delay the access walk while keeping the
same boarding time: a later arrival at the platform can mean a later train.
Scheduled services can already have time before leaving (`originWaitSeconds`).
That is distinct from the estimated platform wait (`initialWaitSeconds`).

OTP 2.9's wide search can omit earlier frequency itineraries. Whenever a result
contains a non-exact frequency trip, `plan_connection` also searches `PT1M` from
the same requested departure. It preserves the modes, preferences and station
constraints, combines both sets of complete itineraries and ranks them normally.
This applies to two-point routing and multi-stop replay. The additional query is
capped at 15 seconds and the remaining original query budget. On failure, the
wide-search result survives with a warning; it is not claimed to be optimal.
Later scheduled services remain available. No feed times, walking times, buffers,
train waits or transfer times are subtracted or fabricated.

Reproduction: departure `2026-10-02T11:00+08:00`, origin Electric Road Market near
Fortress Hill `(22.28943, 114.19364)`, destination LOHAS Park station
`(22.29559, 114.26873)`, MTR only, fastest. The wide search returned
`11:15:00–11:50:55`; the additional search recovered `11:00:00–11:35:55`.
Both retained the model's 246-second first train allowance. A request at 11:04
returned `11:04:00–11:39:55`, confirming that leaving four minutes later does not
eliminate the allowance. These are experimental estimates, not live predictions.

Replay the captured engine responses without a server or rebuild:

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_departure_window.py' -v
```

The fixture is `tests/fixtures/fortress_hill_frequency_wait.json`. It retains both
raw itineraries, including geometry and leg times. Restart the Python server to
load the fix; no GTFS/OTP graph rebuild or Swift app rebuild is needed. The
multi-stop cache includes the server source version, so old searches are not
reused after restart. Existing saved routes are snapshots and must be replanned.

References: [GTFS frequency semantics](https://gtfs.org/documentation/schedule/reference/#frequenciestxt)
and [OTP's reported frequency search issue](https://github.com/opentripplanner/OpenTripPlanner/issues/6872).
