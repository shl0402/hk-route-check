# Multi-stop test mode

Start the normal server (`python3 run.py`), then open **http://127.0.0.1:8000/multi**.
Load 4, 8, 12, 20, 50 or 100 sample shop/attraction jobs (including rural locations), or add locations by search/map. Each job has its own work duration and optional appointment window.

- **Workers:** omit `workers` to find a count using the shared `shift` template. Supply `workers` to choose a subset of specific available workers with different shifts, bases and breaks.
- **Bases:** `startLocation` and `endLocation` are independent for every worker. Omitted/null means starting at their first job or finishing at their last. Locations use `lat`, `lon`, optional `name`.
- **Breaks:** null = no break. `minutes` must fit entirely between `earliest` and `latest`. A fixed 12:00–12:30 break uses minutes=30, earliest=12:00, latest=12:30. No travel or work during breaks. Breaks are mandatory for every selected worker, even on a short route.
- **Windows:** the whole job must finish inside its window; windows are optional.
- **Limits:** Fast/Full: 1–20 jobs and up to 20 worker profiles. More jobs: 1–100 jobs and up to 100 worker profiles; shifts up to 16 hours within one 24-hour planning period. No cars.

## Planning modes

The page defaults to **Fast · up to 20**. **[More jobs · up to 100](LARGE_MODE.md)** uses R5 + OR-Tools Routing followed by exact fixed-order scheduling and OTP checks. **Full pair search · OTP** remains available with the same inputs. API clients set `"planningMode":"fast"`, `"full"` or `"large"`; omission preserves the original full mode.

Fast mode uses R5py 1.1.7 / R5 7.5.1 to seed OR-Tools with a batch travel-time matrix. It uses the same GTFS and OSM files; transport subsets are filtered by route IDs (Light Rail remains distinct from Hong Kong Island tram). No coordinates or transit timings are invented or overwritten.

The matrix uses 90th-percentile travel times across a 10-minute window and requests 20 Monte Carlo draws for frequency services. Missing connections are retried across 60 minutes. The higher percentile reduces optimistic waits and repeated repair rounds. These are approximate search costs, **not checked departures**. A lower sampling effort trades matrix precision for speed. OTP replay requests up to six alternatives over a 10-minute departure window, with the original hour-long search as fallback when no valid itinerary survives filtering. It retains the separate complete-MTR query. This saves search time but may miss a better option farther into the departure window. Every selected journey still uses the existing whole-MTR OTP checks, its actual planned departure, full waiting time, source details and schedule audit. Fast mode allows up to ten repair rounds within the overall budget. Existing appointment slack can absorb a longer journey only if it does not cross a break or the next job. If checks fail, the assignment is repaired; an unverified plan is never labelled checked. R5/OTP transfer and walking models may differ, and R5 cannot enforce our custom no-split-MTR rule at the matrix stage. Neither mode proves a global minimum.

First use downloads the official checksum-verified R5 engine and builds a network. Later runs reuse the disk network, in-memory worker and matrix cache under `data/optimization/r5/`. That first preparation can take longer than a warmed run; no fixed runtime is promised. Two origins are calculated concurrently. R5 is isolated in a process with a 4 GB Java heap; cancellation terminates native matrix calculation. OTP checks still stop after their current requests finish. The UI shows origin-by-origin matrix progress, candidate assignments, actual-time checks and final routes.

Run the usual `python3 run.py` to install the pinned dependencies. An online first fast run prepares the engine; keep its local cache for subsequent offline runs. Do not import third-party R5 cache files, which include serialized objects.

## Repeatable timing tests

Uncheck **Reuse travel results**, or set `"useTravelCache":false` in the API. All modes then bypass reading and writing OTP journey results, and Fast and More jobs modes also bypass R5 matrix results. This includes repeated checks within repair rounds. Existing caches are preserved; cache hits should be zero. Progress/results identify when caching is off.

Prepared R5/OTP networks and downloaded source data remain reusable. This measures a fresh routing calculation on a prepared server, not first-time installation/network startup. For comparable tests, use the same jobs, date, shifts, modes and budgets, and report whether the routing networks were already loaded.

Benchmark: `python3 scripts/benchmark_multi.py --mode fast --counts 20 --budget 600 --no-cache`.

## API

`POST /api/optimizations` (JSON) returns HTTP 202 with `id` and `statusUrl` immediately:

```json
{
  "planningMode": "fast",
  "useTravelCache": true,
  "shift": {
    "start": "2026-09-24T09:00:00+08:00",
    "end": "2026-09-24T18:00:00+08:00",
    "startLocation": null,
    "endLocation": null
  },
  "break": {
    "minutes": 30,
    "earliest": "2026-09-24T12:00:00+08:00",
    "latest": "2026-09-24T14:00:00+08:00"
  },
  "serviceMinutes": 20,
  "jobs": [
    {"id": "a", "name": "dimsumclub · 點心堂", "lat": 22.4673493, "lon": 114.0042081},
    {"id": "b", "name": "Tamjai Yunnan Mixian · 譚仔雲南米線", "lat": 22.4109527, "lon": 113.9687077, "serviceMinutes": 35}
  ],
  "modes": ["mtr", "bus", "ferry", "light_rail", "tram", "funicular"],
  "solverSeconds": 10,
  "maxRounds": 10,
  "maxRuntimeSeconds": 600
}
```

Use a date covered by `/api/status`. Empty `modes` means walking only.

For individual workers, add e.g. `"workers":[{"id":"Sam","start":"2026-09-24T10:00:00+08:00","end":"2026-09-24T17:00:00+08:00","startLocation":{"lat":22.3192,"lon":114.1694},"endLocation":null,"break":null}]`. Missing worker fields inherit the shared template; worker `break:null` disables the shared break for that worker.

| Endpoint | Purpose |
|---|---|
| `GET /api/optimizations/{id}?after=0` | Status, ordered progress events, final result. Pass the latest event `seq` on the next poll. |
| `POST /api/optimizations/{id}/cancel` | Stop after current OTP requests finish; completed pairs stay cached. |
| `GET /api/optimization/example?count=4` | Reproducible test request from sourced OSM shops/attractions, including rural locations. |
| `/multi?run={id}` | Reopen a completed result or follow a running job. |

Only one optimisation runs at a time; another submission receives HTTP 409. Oversized/invalid inputs receive HTTP 400. Progress stages: `validate_input`, `matrix`, `solve`, `validate`, `repair`, `complete` (or `error`/`cancelled`). During a run the map previews completed OTP paths and provisional visiting orders; final display clears those previews.

## What the algorithm guarantees

1. Build directed R5 (Fast/More jobs modes) or OTP (full mode) costs at the earliest shift start, including worker bases. Cache by exact coordinates, departure, transport modes and active data/routing version.
2. OR-Tools CP-SAT (Fast/Full) or Routing with fixed-order CP-SAT (More jobs) assigns all jobs and orders visits, prioritising worker count over total travel and completion time. Worker breaks cannot overlap any job or travel interval.
3. Replay each chosen journey through OTP at its planned departure. **All waiting counts**, including waiting before the first boarding. Original two-point display rules are unchanged.
4. If a journey takes longer, update its cost and repair that assignment; search another assignment if necessary. Return `feasible:true` only after departure-time checks pass.

`minimumWorkersProven` is always false: a static matrix is only a search approximation, even when OR-Tools proves its own sampled model optimal. Costs for untested departure times can differ. OTP’s returned candidates and the source timetable are also not guarantees of real-world arrival times. The existing whole-journey MTR rules and provenance remain in use.

If no checked result is found, `feasible:false` accompanies `no_validated_solution`, `needs_review`, `routing_timeout`, or `budget_exceeded`. Increase the budget, change constraints or switch planning mode; Fast/Full support 20 jobs and More jobs supports 100. A time limit is not proof that the jobs are impossible. The overall limit is cooperative: stopping waits for in-flight routing to finish. Each OTP request has an 80-second timeout; a pair may require multiple requests and wait for the shared routing lock, so stopping can take longer than 80 seconds.

Completed pair cache: `data/optimization/otp-pairs.sqlite3`. Completed runs: `data/optimization/{id}.json`. Both stay local and are excluded from Git. Reloading the page reconnects; restarting the server stops an in-flight search, but cached pairs and completed results survive.

## Measurements

With the server running, reproduce the cases with `python3 scripts/benchmark_multi.py --mode fast --counts 4,8,20 --budget 600`. Results and requests stay in `data/optimization/benchmarks/`. Existing pair cache is reused.

Larger-job measurements are recorded in [LARGE_MODE_RESULTS.md](LARGE_MODE_RESULTS.md). Fast-mode measurements are recorded in [FAST_MODE_RESULTS.md](FAST_MODE_RESULTS.md); earlier full-mode results are in [MULTI_STOP_RESULTS.md](MULTI_STOP_RESULTS.md). They separate matrix, solver and departure-validation time, and identify cache reuse. These are measurements on the current machine, not service-level promises.

Sample coordinates are retained in `route_checker/multi_examples.json`, with original OSM feature URLs and snapshot date. All 100 are non-transit destinations. Published opening hours are not assumed; job windows in these synthetic workloads are user-defined. Distances to MTR are straight-line context only, never routing costs.
