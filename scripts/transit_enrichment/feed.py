"""Streaming GTFS helpers: never load the large MTR all-pairs stop table."""
import csv
import hashlib
import io
import json
from collections import defaultdict
from itertools import groupby
from pathlib import Path


def rows(archive, name):
    if name not in archive.namelist():
        return iter(())
    return csv.DictReader(io.TextIOWrapper(archive.open(name), encoding='utf-8-sig'))


def write_rows(archive, name, fields, values):
    with archive.open(name, 'w') as binary:
        with io.TextIOWrapper(binary, encoding='utf-8', newline='') as text:
            writer = csv.DictWriter(text, fields, lineterminator='\n')
            writer.writeheader()
            writer.writerows(values)


def seconds(value):
    h, m, s = map(int, value.split(':'))
    if h < 0 or not 0 <= m < 60 or not 0 <= s < 60:
        raise ValueError('Invalid GTFS time: ' + value)
    return h * 3600 + m * 60 + s


def clock(value):
    value = int(value)
    return f'{value // 3600:02d}:{value // 60 % 60:02d}:{value % 60:02d}'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.part')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def pattern_key(route_id, stop_ids):
    return hashlib.sha256(json.dumps([route_id, stop_ids]).encode()).hexdigest()[:20]


def surface_tables(archive):
    routes = [r for r in rows(archive, 'routes.txt') if r['route_type'] == '3']
    ids = {r['route_id'] for r in routes}
    trips = [t for t in rows(archive, 'trips.txt') if t['route_id'] in ids]
    trip_ids = {t['trip_id'] for t in trips}
    times = [r for r in rows(archive, 'stop_times.txt') if r['trip_id'] in trip_ids]
    stop_ids = {r['stop_id'] for r in times}
    return {'routes': routes, 'trips': trips, 'stop_times': times,
            'stops': [s for s in rows(archive, 'stops.txt') if s['stop_id'] in stop_ids]}


def index_patterns(tables):
    trips = {t['trip_id']: t for t in tables['trips']}
    trip_times = defaultdict(list)
    for row in tables['stop_times']:
        trip_times[row['trip_id']].append(row)
    patterns = {}
    for tid, stop_times in trip_times.items():
        stop_times.sort(key=lambda r: int(r['stop_sequence']))
        trip = trips[tid]
        key = pattern_key(trip['route_id'], [r['stop_id'] for r in stop_times])
        patterns.setdefault(key, {'route_id': trip['route_id'],
                                 'stop_ids': [r['stop_id'] for r in stop_times],
                                 'trip_ids': []})['trip_ids'].append(tid)
    return trips, trip_times, patterns
