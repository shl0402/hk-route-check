# Larger-job timing tests

Hong Kong samples, 24 September 2026, 09:00–18:00, 20 minutes work per job, 30-minute break within 12:00–14:00, open start/end locations, all public-transport modes. **Travel-result cache off**; prepared R5/OTP networks retained. No live arrival data.

| Jobs | Checked result | Workers found | Matrix | Solver | OTP checks | Total | OTP calls | Cache hits |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 50 | Yes; 3 rounds | 7 | 105.7 s | 16.9 s | 111.9 s | **234.5 s** | 130 | **0** |
| 100 | Yes; 3 rounds | 13 | 194.3 s | 15.3 s | 238.1 s | **447.7 s** | 264 | **0** |

Worker counts are heuristic, not proven minima. All returned jobs, shifts, breaks and selected departure-time journeys passed the final audit. These measurements describe this machine and feed, not a runtime guarantee.

```sh
python3 scripts/benchmark_multi.py --mode large --counts 50,100 --budget 900 --no-cache
```

The 50-job case was measured with the same engine through the Python harness before installing the mode. The 100-job test used the public API: run `f76ab5a8f66946abb43b2db1ca607ac8`. Both cases used a 20-second assignment budget per round and a 900-second overall limit.

Validation: **61 project tests + 6 scraper tests passed**. Browser checks covered loading 100 sample places, automatic mode selection, request generation, matrix pagination and selected previews. The automated suite also checks a synthetic 100-job schedule, fixed/flexible/no breaks, differing shifts, appointment windows, fixed bases, missing connections, time-dependent repairs and cancellation.
