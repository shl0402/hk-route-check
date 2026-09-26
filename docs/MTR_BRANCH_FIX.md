# MTR branch routing fix · 26 September 2026

- Cause: through-train timetables excluded LOHAS Park's non-peak change at Tseung Kwan O, although the cached MTR API explicitly describes it.
- Fix: compile that service-note-backed variant against each section's dated timetable. Keep the complete API journey duration; do not add segment estimates or extra internal waiting. Include the same-line change in instructions.
- LOHAS Park → HKU, Saturday 07:32: **71.8 → 49.8 minutes**, with **37 minutes** from the cached MTR journey. Reverse direction verified at **49.8 minutes** too.
- Active GTFS and OTP graph rebuilt; independent validation: **0 errors**. **66 unit tests + 4 live MTR tests passed**.
- Normal `run.py build` includes the fix. Both OTP and R5 caches change with the feed version. These remain operator journey estimates, not live train timings.
