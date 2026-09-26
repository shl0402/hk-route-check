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

## Alternatives

1. Search the selected transport. A separate MTR search preserves complete station-pair journeys.
2. When enabled, additionally search the selected non-MTR transport and bus/minibus alone, where applicable. No unchecked mode is added.
3. Combine valid results, remove repeated paths, and sort **all** results by the chosen preference. Return up to `maxResults` (1–10; default 6).

Slower alternatives can survive because each transport subset is searched separately. More candidates in one OTP search alone would not recover routes that OTP excludes as worse. Six is a limit, not a guarantee of six results or one of every mode.

`includeAlternatives: false` skips the extra subset searches. An unavailable optional search is reported in `warnings`; completed results remain usable. Multi-stop optimisation skips these extra searches automatically.

The response includes `itineraries`, `searches`, `warnings`, `elapsedSeconds`, and source explanations. Sorting uses scheduled journey duration for `fastest`, transfers then duration for `transfers`, and walking distance then duration for `walking`. The displayed total still excludes the first visible boarding wait; this display adjustment does not change sorting.
