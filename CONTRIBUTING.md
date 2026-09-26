# Contributing

This is a research prototype for a future worker-routing optimiser. Current scope: two-location routing, source verification and worker schedules (Fast/Full: 1–20 jobs; More jobs: up to 100).

Start with `python3 run.py`. Keep source provenance and fail on missing evidence rather than inserting guessed timings. MTR line changes belong inside a whole cached station-pair journey; preserve its API total and do not add another modelled internal boarding wait.

Fast checks, without downloading transport data:

```sh
python3 -m pip install -r requirements.txt
python3 scripts/check_source_tests.py
```

A full integration check requires source data and Java 25: `python3 run.py build --offline`, then `python3 run.py check`. Existing snapshot-specific tests beside the collectors and checker need the corresponding cache/build; they are not the source-only CI suite.

For a bug report, include the departure date/time, public origin/destination, selected modes, expected/actual result, and relevant source URL. Avoid personal journeys or credentials. For scraper changes, add a small synthetic table fixture covering the rule; do not commit entire scraped pages.

Before publishing, inspect `git status` and `git diff --cached`. `.gitignore` intentionally excludes data, graphs, caches, virtual environments and research backups. Add source files normally; do not use `git add -f data`.
