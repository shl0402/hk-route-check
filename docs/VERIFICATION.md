# Release preparation checks

Test snapshot: September 2026. Experimental service window: **2026-09-22–2026-10-21**.

| Check | Result |
| --- | ---: |
| Raw cache files | 16,681 |
| Compressed source cache | 129.8 MB |
| Bus/minibus articles parsed | 1,913 |
| Bus/minibus routes receiving accepted wiki schedules | 334 |
| Heavy-rail wiki lines applied | 10 |
| Rail wiki trip templates | 2,141 |
| Cached MTR whole-journey paths compiled | 21,105 |
| MTR distance/speed fallbacks | 0 |
| Independent GTFS validation errors | 0 |

The rebuild starts from raw source responses, without a prebuilt GTFS, graph, parsed article directory, or personal coordinate cache. It uses official MTR station metadata for coordinates. The optional cache was exported and imported through the public wrapper. A repeat build verified and reused all nine stages without recompiling GTFS or OTP. The final graph was built with the official OTP 2.9.0 binary, verified against its published SHA-256 checksum.

A fresh Python 3.12 virtual environment successfully installed `requirements.txt`. All 15 source-only tests and the inline JavaScript syntax check passed.

Source-only checks cover cache round trips, corrupted checksums, unsafe paths/symlinks, executable-file rejection, government JSON with a byte-order mark, changed build artifacts, active-build source dates, dynamic service-window boundaries, and six timetable-parser cases. Seven additional backend timing tests passed.

Live acquisition smoke checks cover official MTR/LR inventories, station metadata, published headways, one complete MTR journey, government bus/minibus route metadata, and representative bus/rail wiki pages. **A second full fresh scrape of every remote route/pair was not performed.** Future upstream changes can require parser updates.

Screenshots are real app captures of Mong Kok → North Point. They are examples from the cached snapshot, not live arrival information. Remaining Light Rail, walking, transfer and bus-intermediate-time assumptions are documented in the checker and README.

Live rebuilt-server check: **Mong Kok → North Point: 22 minutes, one internal line change, zero counted transfers, zero additional internal transfer wait**. The service window and current-time default were also checked in the browser. The temporary test servers were stopped after verification.
