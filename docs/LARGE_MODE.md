# More jobs mode · 1–100 jobs

Choose **More jobs · up to 100** on `/multi`, or send `"planningMode":"large"` to the existing API. Loading a 50- or 100-place sample selects it automatically. Inputs, cancellation, progress, route previews and source details work as before.

| Mode | Jobs | Assignment method |
|---|---:|---|
| Fast | 1–20 | R5 matrix + CP-SAT |
| Full pair search | 1–20 | OTP matrix + CP-SAT |
| More jobs | 1–100 | R5 matrix + OR-Tools Routing + fixed-order CP-SAT |

The larger mode uses cheapest insertion and guided local search. A worker's fixed cost outweighs all travel costs, so the search prioritises fewer workers. It then schedules each job, journey and break as an uninterrupted interval. If an order does not fit, interchangeable workers can share shorter sections of that order. Different worker profiles retain their own shifts and bases.

R5 samples a 10-minute departure window. Unlike Fast mode, a wider 60-minute retry runs only for origins with no other reachable destinations. This avoids repeated broad searches for one difficult destination. Missing pairs stay unavailable to the search, not zero-cost; infrequent connections may be missed, which can increase worker count or prevent a result.

Every selected journey is checked through OTP at its planned departure, including waits. Longer or unavailable journeys trigger repairs and another check. Only a complete, checked schedule is returned as feasible. The worker count is the **fewest found**, not a proven minimum; repairs may need extra workers. Tight appointment/break windows can defeat the heuristic even when another ordering would work.

The default assignment budget is 20 seconds per round, with up to 10 repair rounds and a 10-minute overall budget. Routing requests may finish after cancellation or the budget expires. Above 100 jobs is rejected with an explanation; no jobs are silently dropped. A 100-job limit is a supported input size, not a runtime guarantee.

Measured results: [LARGE_MODE_RESULTS.md](LARGE_MODE_RESULTS.md).

## Repeat a timing test

```sh
python3 scripts/benchmark_multi.py --mode large --counts 50,100 --budget 900 --no-cache
```

Travel-result caching is bypassed, including during repairs. Prepared R5/OTP networks are reused. Report the matrix, solver and OTP-check times separately; first network preparation adds startup time.

Samples contain real OSM restaurants/cafes around the original urban and rural examples. Coordinates and source links are preserved. Rebuild the additional samples after generating the place index:

```sh
python3 scripts/build_multi_examples.py
```

This retains the original first 20 places and deterministically selects 80 more, at least 150 m from previously selected places. It does not assume opening hours or invent destinations.
