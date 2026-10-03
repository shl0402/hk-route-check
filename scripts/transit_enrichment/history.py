#!/usr/bin/env python3
"""Pinned, verified snapshots of experimental ETA-derived stop-pair estimates.

This independently written reader does not reproduce the upstream estimator.
The estimates are not measurements, schedules, or route-specific travel times.
No function here changes a GTFS feed. Consumers must verify operator stop order,
reject route-path ambiguity, preserve published timing anchors, and label any
interpolation using these weights as an estimate.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time

REPOSITORY = 'HK-Bus-ETA/hk-bus-time-between-stops'
SOURCE_URL = 'https://github.com/' + REPOSITORY
# Reviewed matching algorithm: closest nonnegative ETA difference, 10% margin,
# 90/10 exponential smoothing; no vehicle IDs, observation counts or dates.
REVIEWED_CODE_COMMIT = '2238d13a6fdf5743b3ec3187f36b5a23e667a76a'
SCHEMA = 'directed-stop-id-to-stop-id-seconds/v1'
MIN_SECONDS, MAX_SECONDS = 5, 3960
MAX_FILE_BYTES = 5_000_000
POLICY = ('Experimental ETA-derived estimates; not measured travel times. '
          'Stop-pair keys omit route, service variant and vehicle identity. '
          'Per-pair dates, observation counts and dispersion are unavailable. '
          'Use only as checked interpolation weights between existing timing '
          'anchors; never replace authoritative rail journey timings.')


def raw_directory(root):
    return Path(root) / 'data/transit_enrichment/raw/history'


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.part')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git_sha(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def parse_pairs(data):
    """Validate structure; retain plausible source values with rejection counts.

    Five to 3,960 seconds is the reviewed estimator's matching interval with
    its 10% margin, not an accuracy guarantee. Raw rejected records stay cached.
    """
    if not isinstance(data, dict) or not data:
        raise ValueError('Historical timing root must be a nonempty object')
    clean, rejected, total = {}, {}, 0
    # Asterisk is used in MTR feeder service variants such as K53*-U030.
    # IDs are object keys only, never file paths.
    identity = re.compile(r'[A-Za-z0-9:*_-]{1,64}')
    for origin, destinations in data.items():
        if not isinstance(origin, str) or not identity.fullmatch(origin):
            raise ValueError('Unexpected historical origin ID')
        if not isinstance(destinations, dict):
            raise ValueError('Historical destinations must be an object')
        for destination, seconds in destinations.items():
            total += 1
            if not isinstance(destination, str) or not identity.fullmatch(destination):
                raise ValueError('Unexpected historical destination ID')
            reason = None
            if origin == destination:
                reason = 'self_pair'
            elif isinstance(seconds, bool) or not isinstance(seconds, (float, int)):
                reason = 'not_numeric'
            elif not math.isfinite(seconds):
                reason = 'not_finite'
            elif not MIN_SECONDS <= seconds <= MAX_SECONDS:
                reason = 'outside_reviewed_algorithm_bounds'
            if reason:
                rejected[reason] = rejected.get(reason, 0) + 1
            else:
                clean.setdefault(origin, {})[destination] = float(seconds)
    return clean, dict(raw_pairs=total, accepted_pairs=sum(map(len, clean.values())),
                       rejected_pairs=sum(rejected.values()), rejected_reasons=rejected)


def checked_file(directory, name, metadata):
    path = directory / name
    content = path.read_bytes()
    if len(content) != metadata.get('bytes') or sha(content) != metadata.get('sha256'):
        raise ValueError('Historical cache checksum mismatch: ' + name)
    if metadata.get('git_blob_sha') and git_sha(content) != metadata['git_blob_sha']:
        raise ValueError('Historical cache git blob mismatch: ' + name)
    return content


def migrate_audit_text(directory, manifest):
    """Keep downloaded source evidence as non-executable .txt cache artifacts."""
    changed = False
    for original in ('README.md', 'LICENSE', 'main.py', 'eta.py'):
        old, new = 'source/' + original, 'source/' + original + '.txt'
        meta = manifest.get('files', {}).get(old)
        if meta is None:
            continue
        # Verify the original bytes before moving; support a process interrupted
        # after the atomic rename but before its manifest checkpoint.
        present = old if (directory / old).exists() else new
        checked_file(directory, present, meta)
        if present == old:
            if (directory / new).exists():
                checked_file(directory, new, meta)
            (directory / old).replace(directory / new)
        manifest['files'][new] = manifest['files'].pop(old)
        changed = True
    return changed


def fetch(root, offline=False, refresh=False, hourly=True, workers=3):
    """Collect overall and all 168 HKT day/hour files at one immutable commit.

    Manifest updates after each successful file make interrupted downloads
    resumable. Normal reruns reuse the pinned commit; --refresh selects a new
    one. Offline replay never repairs a missing or tampered file over network.
    """
    if offline and refresh:
        raise ValueError('Cannot refresh historical sources offline')
    directory = raw_directory(root)
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / 'manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    if manifest and manifest.get('format') != 1:
        raise ValueError('Unsupported historical source manifest')
    if manifest and migrate_audit_text(directory, manifest):
        save(manifest_path, manifest)
    import requests

    def download(url):
        for attempt in range(3):
            try:
                with requests.get(url, timeout=(15, 60), stream=True,
                                  headers={'User-Agent': 'map-routing-source-verification/1'}) as response:
                    response.raise_for_status()
                    parts, size = [], 0
                    for part in response.iter_content(65536):
                        size += len(part)
                        if size > MAX_FILE_BYTES:
                            raise ValueError('Historical artifact exceeds size bound: ' + url)
                        parts.append(part)
                    return b''.join(parts)
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)

    api = 'https://api.github.com/repos/' + REPOSITORY
    if manifest is None or refresh:
        if offline:
            raise ValueError('Missing historical manifest; run an online fetch first')
        source = json.loads(download(api + '/commits/main'))
        if source['sha'] != REVIEWED_CODE_COMMIT:
            raise ValueError('Historical estimator changed; review its algorithm before refreshing')
        snapshot = json.loads(download(api + '/commits/pages'))
        # 30k small prefix shards can make the recursive tree exceed 5 MB;
        # request only aggregate files directly by pinned commit instead.
        manifest = dict(format=1, repository=SOURCE_URL, schema=SCHEMA,
                        source_code_commit=source['sha'],
                        source_code_date=source['commit']['committer']['date'],
                        data_commit=snapshot['sha'],
                        data_commit_date=snapshot['commit']['committer']['date'],
                        created_at=datetime.now(timezone.utc).isoformat(), files={},
                        day_convention='0=Sunday or HK public holiday; 1=Monday ... 6=Saturday',
                        hour_timezone='Asia/Hong_Kong', units='seconds',
                        license='Repository declares GPL-3.0; upstream source/data terms retained',
                        policy=POLICY, complete=False)
        save(manifest_path, manifest)
    if manifest['source_code_commit'] != REVIEWED_CODE_COMMIT:
        raise ValueError('Historical cache uses an unreviewed source algorithm')
    data_commit = manifest['data_commit']
    if not re.fullmatch(r'[0-9a-f]{40}', data_commit):
        raise ValueError('Historical data commit is not pinned')
    artifacts = {'times/all.json': data_commit}
    if hourly:
        artifacts.update({f'times_hourly/{day}/{hour:02d}/all.json': data_commit
                          for day in range(7) for hour in range(24)})
    artifacts.update({'source/' + name + '.txt': REVIEWED_CODE_COMMIT
                      for name in ('README.md', 'LICENSE', 'main.py', 'eta.py')})
    pending = []
    for name, commit in artifacts.items():
        meta = manifest['files'].get(name)
        path = directory / name
        if meta and path.exists():
            checked_file(directory, name, meta)
            if meta.get('commit') != commit:
                raise ValueError('Historical artifact commit disagrees with snapshot: ' + name)
        elif offline:
            raise ValueError('Missing historical cache artifact: ' + name)
        else:
            pending.append((name, commit))

    def acquire(item):
        name, commit = item
        remote = name.removeprefix('source/')
        if name.startswith('source/'):
            remote = remote.removesuffix('.txt')
        url = f'https://raw.githubusercontent.com/{REPOSITORY}/{commit}/{remote}'
        content = download(url)
        quality = None
        if name.endswith('.json'):
            _, quality = parse_pairs(json.loads(content))
        if not content:
            raise ValueError('Empty historical source: ' + name)
        metadata = dict(url=url, commit=commit, sha256=sha(content),
                        git_blob_sha=git_sha(content), bytes=len(content),
                        retrieved_at=datetime.now(timezone.utc).isoformat())
        if quality:
            metadata['quality'] = quality
        return name, content, metadata

    if pending:
        manifest['complete'] = False
        save(manifest_path, manifest)
        with ThreadPoolExecutor(max_workers=max(1, min(4, workers))) as pool:
            futures = [pool.submit(acquire, item) for item in pending]
            errors = []
            for index, future in enumerate(as_completed(futures), 1):
                try:
                    name, content, meta = future.result()
                except Exception as error:
                    # Still persist independently successful files so a single
                    # failed download/schema never discards the entire batch.
                    errors.append(str(error))
                    continue
                path = directory / name
                path.parent.mkdir(parents=True, exist_ok=True)
                temp = path.with_suffix(path.suffix + '.part')
                temp.write_bytes(content)
                temp.replace(path)
                manifest['files'][name] = meta
                save(manifest_path, manifest)
                print(f'Historical estimates {index}/{len(pending)}: {name}', flush=True)
            if errors:
                raise ValueError('Historical fetch incomplete; rerun to resume: ' + '; '.join(errors[:5]))
    manifest['complete'] = True
    manifest['hourly_complete'] = all(f'times_hourly/{d}/{h:02d}/all.json' in manifest['files']
                                      for d in range(7) for h in range(24))
    save(manifest_path, manifest)
    return manifest


def normalize(root, include_hourly=False):
    """Return verified overall weights and lazy hourly artifact references."""
    directory = raw_directory(root)
    manifest = json.loads((directory / 'manifest.json').read_text())
    if not manifest.get('complete') or manifest.get('source_code_commit') != REVIEWED_CODE_COMMIT:
        raise ValueError('Incomplete or unreviewed historical source snapshot')
    data = json.loads(checked_file(directory, 'times/all.json', manifest['files']['times/all.json']))
    overall, quality = parse_pairs(data)
    hourly_files = {}
    hourly_values = {}
    for name, meta in manifest['files'].items():
        match = re.fullmatch(r'times_hourly/([0-6])/(\d{2})/all.json', name)
        if not match:
            continue
        key = f'{match[1]}/{match[2]}'
        hourly_files[key] = dict(path=name, quality=meta.get('quality'), sha256=meta['sha256'])
        if include_hourly:
            hourly_values[key], _ = parse_pairs(json.loads(checked_file(directory, name, meta)))
    source = {key: manifest[key] for key in ('repository', 'schema', 'source_code_commit',
              'data_commit', 'data_commit_date', 'day_convention', 'hour_timezone', 'license', 'policy')}
    source.update(manifest_sha256=sha((directory / 'manifest.json').read_bytes()),
                  sha256=manifest['files']['times/all.json']['sha256'],
                  snapshot=manifest['files']['times/all.json']['retrieved_at'][:10],
                  source_url=manifest['files']['times/all.json']['url'])
    return dict(overall=overall, hourly=hourly_values, hourly_files=hourly_files,
                source=source, quality=quality)


def load_hour(root, day, hour):
    if type(day) is not int or type(hour) is not int or day not in range(7) or hour not in range(24):
        raise ValueError('Expected source weekday 0..6 and local hour 0..23')
    directory = raw_directory(root)
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest.get('source_code_commit') != REVIEWED_CODE_COMMIT:
        raise ValueError('Unreviewed historical source algorithm')
    name = f'times_hourly/{day}/{hour:02d}/all.json'
    if name not in manifest['files']:
        return {}, dict(missing_hour=True)
    return parse_pairs(json.loads(checked_file(directory, name, manifest['files'][name])))


def route_coverage(stop_ids, values):
    """Evidence only: do not fabricate a total for a partly covered route."""
    segments = []
    for origin, destination in zip(stop_ids, stop_ids[1:]):
        seconds = values.get(origin, {}).get(destination)
        segments.append(dict(origin=origin, destination=destination, seconds=seconds))
    available = [s['seconds'] for s in segments if s['seconds'] is not None]
    return dict(segments=len(segments), covered=len(available),
                total_seconds=sum(available) if len(available) == len(segments) and segments else None,
                missing_pairs=[[s['origin'], s['destination']] for s in segments if s['seconds'] is None])


def research(root, identity_path=None, gtfs_path=None):
    """Recreate coverage and timing-conflict evidence from downloaded artifacts.

    Route identity here is a candidate comparison, not GTFS merge permission.
    The consumer's operator/coordinate/shape checks remain mandatory.
    """
    import csv
    import io
    import zipfile
    from collections import defaultdict
    root = Path(root)
    result = normalize(root)
    identity_path = Path(identity_path or root / 'data/transit_enrichment/raw/identity/routeFareList.min.json')
    identity = json.loads(identity_path.read_text())
    selected = []
    for key, route in identity['routeList'].items():
        if route['route'] not in ('N796', '98D', '74X') or str(route.get('serviceType')) != '1':
            continue
        for operator, stops in route['stops'].items():
            selected.append(dict(key=key, route=route['route'], operator=operator,
                                 bound=route['bound'].get(operator), gtfs_id=route.get('gtfsId'),
                                 catalogue_journey_minutes=route.get('jt'), stop_ids=stops,
                                 overall=route_coverage(stops, result['overall']), hourly={}))
    for day in range(7):
        for hour in range(24):
            values, _ = load_hour(root, day, hour)
            for route in selected:
                route['hourly'][f'{day}/{hour:02d}'] = route_coverage(route['stop_ids'], values)
    gtfs_path = Path(gtfs_path or root / 'data/generated/hk-transit-EXPERIMENTAL.gtfs.zip')
    source_spans = defaultdict(set)
    if gtfs_path.exists():
        with zipfile.ZipFile(gtfs_path) as z:
            def rows(name):
                return csv.DictReader(io.TextIOWrapper(z.open(name), encoding='utf-8-sig'))
            ids = {r['gtfs_id'] for r in selected if r['gtfs_id']}
            trips = {t['trip_id']: t for t in rows('trips.txt') if t['route_id'] in ids}
            times = defaultdict(list)
            for row in rows('stop_times.txt'):
                if row['trip_id'] in trips:
                    times[row['trip_id']].append(row)
            def seconds(value):
                h, m, s = map(int, value.split(':'))
                return h * 3600 + m * 60 + s
            for trip_id, stops in times.items():
                stops.sort(key=lambda row: int(row['stop_sequence']))
                duration = seconds(stops[-1]['arrival_time']) - seconds(stops[0]['departure_time'])
                source_spans[trips[trip_id]['route_id']].add((len(stops), duration))
    for route in selected:
        route['existing_gtfs_spans'] = [dict(stops=count, seconds=seconds)
                                        for count, seconds in sorted(source_spans[route['gtfs_id']])]
        del route['stop_ids']
    directory = raw_directory(root)
    manifest = json.loads((directory / 'manifest.json').read_text())
    raw = json.loads(checked_file(directory, 'times/all.json', manifest['files']['times/all.json']))
    rejected_examples = []
    for origin, destinations in raw.items():
        for destination, seconds in destinations.items():
            if destination not in result['overall'].get(origin, {}):
                rejected_examples.append(dict(origin=origin, destination=destination, raw_seconds=seconds))
    wiki = root / 'data/wiki_pilot/articles/9231.json'
    wiki_evidence = None
    if wiki.exists():
        cached = json.loads(wiki.read_text())
        found = re.search(r'行車時間\s*(\d+)分鐘', cached.get('article_text', ''))
        if cached.get('title') == '城巴N796線' and found:
            wiki_evidence = dict(url=cached['source_url'], revision=cached['revision_id'],
                                 snapshot=cached['fetched_at'], raw_sha256=sha(wiki.read_bytes()),
                                 route_minutes=int(found[1]),
                                 status='Secondary-source comparison only; not an operator timing anchor')
    report = dict(format=1, source=result['source'], quality=result['quality'],
                  hourly_files=len(result['hourly_files']),
                  cached_bytes=sum(v['bytes'] for v in manifest['files'].values()),
                  hourly_accepted_pairs=sum(v['quality']['accepted_pairs']
                                            for v in result['hourly_files'].values()),
                  rejected_examples=rejected_examples, route_comparisons=selected,
                  n796_wiki_comparison=wiki_evidence,
                  methodology_findings=[
                      'Upstream pairs the closest valid downstream ETA with an upstream ETA; there is no vehicle tracking identity.',
                      'The returned difference has a 10% margin and 90/10 exponential smoothing, not an arithmetic sample mean.',
                      'Distances help restrict matching for segments over 1.5 km; a value is not a separately observed stop-to-stop trip.',
                      'No per-pair update timestamp, sample count, variance, operator or service-variant key is exported.',
                      'Source day 0 combines Sundays and Hong Kong public holidays; source hours use local HKT wall clock.',
                      'An hourly value is sampled by request hour, not necessarily departure hour; a trip may cross several hour bins.',
                      'All-day values can mix traffic regimes and route variants that share the same directed stop pair.',
                      'Mainline MTR and light rail are excluded from timing application; existing operator journey data is stronger.',
                  ],
                  application_policy=POLICY,
                  timing_guard_recommendation=dict(raw_span_to_existing_anchor_ratio=[2/3, 1.5],
                    reason='Conservative compatibility guard, not a statistical confidence interval or independent accuracy proof. Large conflicts remain evidence-only.'),
                  identity_snapshot_sha256=sha(identity_path.read_bytes()),
                  comparison_gtfs_sha256=sha(gtfs_path.read_bytes()) if gtfs_path.exists() else None)
    save(root / 'data/transit_enrichment/history_research.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('fetch', 'check', 'research'))
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--overall-only', action='store_true')
    args = parser.parse_args()
    if args.action == 'fetch':
        fetch(args.root, args.offline, args.refresh, not args.overall_only)
    if args.action == 'research':
        research(args.root)
    result = normalize(args.root)
    print(json.dumps(dict(source=result['source'], quality=result['quality'],
                          hourly_files=len(result['hourly_files'])), indent=2))


if __name__ == '__main__':
    main()
