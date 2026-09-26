# Fast mode: measured results

24 September 2026 · local Mac · same GTFS/OSM as the two-point checker.

| Jobs | Matrix | Assignment | OTP checks | Total | Workers | Checked |
|---:|---:|---:|---:|---:|---:|:---:|
| 20 | 44.9 s | 30.8 s | 88.6 s | **164.3 s** | **4** | Yes |
| 4 | 8.4 s | <0.1 s | 12.1 s | **20.5 s** | **1** | Yes |

- 20 jobs: 380 directed matrix pairs; 55 OTP pair requests + 10 cache hits across 4 rounds. Final plan: 16 travel journeys.
- 4 jobs: 12 matrix pairs; 5 OTP pair requests + 1 cache hit across 2 rounds.
- Non-station shops/attractions, including rural locations. 09:00–18:00 shifts, 20-minute jobs, flexible 30-minute lunch within 12:00–14:00, free start/end locations, all transport modes.
- Reused the built R5 network and running OTP engine. The 20-job run started with empty matrix and OTP-pair caches; subsequent repair rounds reused their own completed checks. The 4-job run followed it. First network preparation/download is additional.
- Every returned journey was checked at its planned departure; all jobs, shifts and breaks passed the schedule audit. Four workers is the best found, **not a proven minimum**. Runtime varies with jobs, modes and repairs.
- 49 project tests passed, including fast-mode constraints, timing repairs and whole-MTR query preservation.

Runs: `b671035934eb4456a72edf767b1bbdec` (20), `42dc8a1312ae4bf586b246813debcbcb` (4). Requests/results are retained locally in `data/optimization/benchmarks/`.

Reproduce: `python3 scripts/benchmark_multi.py --mode fast --counts 20,4 --budget 600`.
