# Local route checker

Use the [root quick start](../README.md): `python3 run.py` builds and starts the checker. Do not run the old build scripts individually for initial setup.

`index.html` contains the UI; `server.py` serves it, searches local places and queries OTP. Both services bind to localhost. The default ports are 8000 and 8081; Ctrl+C stops services started by this invocation.

The display excludes the first visible boarding wait and retains later waits. MTR line changes remain within one whole station-pair journey and one trip. Timetable intervals are model inputs, not live arrivals. See [source attribution](../DATA_SOURCES.md) and [setup details](../docs/SETUP.md).

Two-point searches show up to 6 distinct routes from the selected transport, ordered by the selected preference. Turn off **Show alternatives** to skip extra transport-subset searches. [API details](../docs/TWO_POINT_API.md).

After routing or builder changes, stop the app and run `python3 run.py build`, `python3 run.py check`, then `python3 run.py`. [Verified rebuild](../docs/BUILD_VERIFICATION.md).
