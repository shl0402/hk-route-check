# Sync the standalone app routing servers

`map_routing` is the source of truth for the routing engine. The HTML prototype
and native w8g each have their own embedded copy; editing upstream does not
update either automatically.

The routing release copies all `route_checker/*.py` modules (including optimizer,
ranking, place search and provider identity), place data, the active validated
GTFS/OTP graph/configuration, pinned OTP JAR, rebuild scripts and source caches.
The common app adapter is `scripts/templates/w8g_routing.py`; it preserves the
app job API while using upstream display durations including waiting, endpoint
coverage warnings and route alternatives. The adapter does not replace Auth,
Supabase, user records, app configuration, HTML or Swift UI files.

## Prepare and install

From map_routing, first build/validate new data when upstream data processing has
changed. A routing Python-only change does not require an OTP graph rebuild.

```sh
python3 run.py check
.venv/bin/python scripts/check_source_tests.py
.venv/bin/python scripts/package_indoor_release.py \
  --target /path/to/w8g --output /tmp/w8g-routing-release
python3 /tmp/w8g-routing-release/apply.py --target /path/to/w8g --check
```

Stop the target app API and its OTP engine before installation. The installer
checks ports 8787 and 8092 by default; supply `--ports` for other configured ports.
It checks old hashes, backs up replaced files, then installs. Prepare a separate
release for app_prototype because its previous hashes and app endpoint differ.

```sh
python3 /tmp/w8g-routing-release/apply.py --target /path/to/w8g
python3 scripts/verify_routing_sync.py --target /path/to/w8g \
  --manifest /tmp/w8g-routing-release/manifest.json --source "$PWD"
```

Keep the release manifest for future drift checks. If upstream changes, rerun the
last check: differences identify what needs updating. Start the app's server
manually with its existing `./start.command`. Do not replace the app's `run.py`
with upstream `run.py`; they intentionally launch different API frontends.

## Rebuild without the upstream checkout

Both app releases include pre-indoor and pre-surface feeds, checksummed public
source archives and pinned tools. Inside either app directory:

```sh
.venv/bin/python -m pip install -r requirements-indoor.txt
.venv/bin/python scripts/rebuild_surface.py
# Or rebuild both indoor and surface/operator layers:
.venv/bin/python scripts/rebuild_indoor.py
```

These write isolated candidates. Both paths apply operator timetable corrections
last, before validation and OTP compilation. Neither starts a server nor replaces
active data automatically. See SURFACE_TIMING.md and ROUTING_ACCURACY.md for data
limits. Full recollection of all upstream timetable sources still uses upstream
run.py and its source-cache workflow.

## Live routing checks

With a temporary route checker running on a separate port against the candidate
graph, run the live checks (these require a running server):

```sh
ROUTE_CHECKER_TEST_URL=http://127.0.0.1:8194 .venv/bin/python -m unittest discover -s route_checker -p test_server.py
.venv/bin/python route_checker/check_routing_regressions.py --url http://127.0.0.1:8194
```

## Boundaries

Existing saved routes/history are snapshots; search again to see new timings.
Core routing logic/data can be identical while the public checker and authenticated
app expose different JSON envelopes and UI. Extra API fields may need later UI
work to render; this release carries timing and endpoint warnings without changing
Swift or prototype screens. Cached optimizer travel times are keyed by the active
feed and planner version so older timing semantics do not survive this update.
