# Build verification · 26 September 2026

| Check | Result |
| --- | --- |
| Source-only tests, without transport data | 71 passed |
| Rebuild from raw cached sources, no prebuilt feed/graph | All 10 stages passed |
| Independent GTFS validator | 0 errors |
| Bus timetable merge checks | 22,015 attributed trips checked |
| Rail timetable merge checks | 2,141 records checked |
| Rebuilt server, LOHAS Park → HKU, 26 Sep 07:32 | 37 min MTR; 49.8 min including walking; 0 external transfers |
| Alternatives enabled | 4 distinct routes: approximately 50, 59, 62, 71 min |
| Alternatives disabled | 1 route: approximately 50 min |
| Active GTFS/OTP graph consistency | Passed |

Rebuild command: `python3 run.py build --offline --start-date 2026-09-17 --days 30`.

Verified on macOS with Python 3.12 and Java 25. Python dependencies and routing tools were already installed. A fresh internet collection was not repeated; this verifies rebuilding from existing raw source caches. See [setup](SETUP.md) for fresh collection and cache import. Validation does not prove real-world timetable accuracy.
