# Bus and minibus source pipeline

New users: run `python3 run.py` from the project root. See [setup](../../docs/SETUP.md).

The folder name is historical. This collector discovers wiki directories, compares route candidates with Transport Department GTFS, caches full article evidence and tracks progress in SQLite. It keeps infoboxes, tables, route text, links and provenance, not only timetable rows.

`audit.py` checks identity and table extraction. `merge_gtfs.py` applies supported, sufficiently matched schedules, retaining government stop order and running-time offsets. Ambiguous matches and unsupported timetable blocks stay out of the overlay. `check_merge.py` verifies retained data, calendars and attribution.

Individual scripts are available for debugging; the root workflow supplies their prerequisites, dates and validation in order. All generated output belongs under ignored `data/wiki_pilot/`.
