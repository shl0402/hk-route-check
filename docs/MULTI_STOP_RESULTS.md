# Rural multi-stop results

Tested 24 September 2026. Non-station shop/attraction jobs; 09:00–18:00, 20 minutes per job, 30-minute flexible break, free start/end, all transport modes. Same local OTP graph; caches reused between cases.

| Jobs | Elapsed | Matrix | Solver | Checks | Result |
|---:|---:|---:|---:|---:|---|
| 4 | 289.6s | 226.27s | 0.03s | 63.33s | 1 worker; checked |
| 8 | 140.3s | — | — | — | OTP timeout; no checked schedule (16/56 pairs completed) |
| 20 | 258.2s | — | — | — | OTP timeout; no checked schedule (29/380 pairs completed) |

- 4 jobs: 16 fresh OTP pairs/checks, 2 cache hits, 2 scheduling rounds.
- No real-world or global optimum guarantee. Larger rural cases are not reliably supported within this budget; no unverified schedule is returned.
- 20 sourced non-transit sample destinations; walking / bus / MTR / Light Rail / ferry icons in worker journeys.
- 40 automated tests passed, including shifts, bases, breaks, time-dependent repair and timeout handling.
- Reproduce: `python3 scripts/benchmark_multi.py --counts 4,8,20 --budget 600`.
