# N796 boarding time · 1 October 2026

**Cause:** N796's explicit departure list was cached but excluded because TD calls the circular route “Tsim Sha Tsui” while the wiki calls it “Mong Kok”. The old feed therefore used non-exact frequency service; OTP added a full 30-minute boarding wait.

**Fix:** verify that the same circular stop sequence visits both places, starts/ends at LOHAS Park, and has daily service. Accept the explicit clock list only when it agrees with every government frequency band. Keep the separate Tsim Sha Tsui-origin special service unchanged. Do not convert other frequency-only services into invented exact timetables.

- 12 normal departures: 23:45; 00:15/45 through 03:15/45; 04:15/35/55.
- 2 existing special departures retained: 00:38 and 01:08.
- Sources: [wiki departure list](https://hkbus.fandom.com/wiki/城巴N796線), corroborated by the [Citybus timetable](https://www.citybus.com.hk/en/uploadedFiles/cust_notice/TS-NWFB-N796-N796-N.pdf) and government frequency bands.
- Terminal departures are scheduled; intermediate-stop times and bus running times remain estimates. These are not live predictions.

For Hemera (`22.29693,114.27043`) → 南洋中心 (`22.29976928,114.17862282`), ready at 03:27 on 1 October, the old result was **82.55 minutes**, including **30 minutes at the first boarding** and **14 minutes before leaving**. Saved before/after responses are under `data/routing-checks/2026-10-01/`.

| Check | Before | After |
| --- | ---: | ---: |
| Total from requested 03:27 | 82.55 min | **52.70 min** |
| First boarding wait | 30 min | **0 min** |
| Suggested leaving time | 03:41:00 | **03:41:09** |
| Arrival | 04:49:33 | **04:19:42** |

The new plan boards N796 around **03:47:14**, using the listed **03:45 terminal departure** and estimated stop offsets. Journey after leaving: **38.55 min**; time before leaving: **14.15 min**. A second test ready at 03:35 catches the same bus and arrives at the same time. Exact results vary with the selected pin.

**Validation:** 136 source tests passed; both live route checks passed; rebuilt GTFS has zero validator errors. The active feed and OTP graph were rebuilt together.

The total still measures arrival minus requested time. “Leave at …” identifies time you can remain at the origin; “after leaving” shows the actual planned journey duration. Moving waiting to the origin alone does not make arrival earlier; using the actual scheduled departure can.

## Reproduce

```sh
python3 run.py build --offline --start-date 2026-09-17 --days 30
python3 run.py check
python3 run.py
# In another terminal:
python3 route_checker/check_n796.py --date 2026-10-01
```

Omit `--offline` if sources are missing. Normal source collection downloads and hashes the Citybus PDF. The existing wiki cache is parsed again during each build; the fix is not a manual ZIP edit.
