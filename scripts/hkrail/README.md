# Rail wiki pipeline

New users: run `python3 run.py` from the project root. See [setup](../../docs/SETUP.md).

`scraper.py` caches the ten heavy-rail line pages, Light Rail lines and related directory evidence. `normalize.py` and `line_adapters.py` interpret their different table layouts; unsupported/ambiguous rules remain excluded. `merge_gtfs.py` applies accepted schedules with source revisions, and `check_merge.py` checks calendars and retained network data.

Wiki schedules supply service windows and intervals. Ride times are handled separately by the MTR API pipeline. Heavy rail uses complete cached origin/destination journeys, including supported internal line changes. Light Rail retains its explicitly labelled segment policy and remaining estimates. Neither is live arrival data.

Generated evidence and reports belong under ignored `data/rail_wiki/`. Use the root workflow instead of manually sequencing the merge scripts.
