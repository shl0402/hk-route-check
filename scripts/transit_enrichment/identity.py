"""Conservative, reproducible crosswalk from TD GTFS visits to operator stops.

No source GTFS ID, route number or nearest stop is accepted by itself.  A match
requires the same operator and route number and the *entire* ordered stop list,
including repeated visits, within the coordinate guards.  Distinct service
variants that cannot be distinguished stay ambiguous.  Stop coordinates are
retained as evidence and are never used to relocate shared GTFS stops.
"""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import io
from itertools import groupby
import json
import math
import re
import unicodedata
from pathlib import Path
import zipfile

REPOSITORY = 'https://github.com/hkbus/hk-bus-crawling'
SOURCES = {
    'database': ('hk-bus-crawling', 'gh-pages', 'routeFareList.min.json'),
    'matching_code': ('hk-bus-crawling', 'master', 'crawling/matchGtfs.py'),
    'merging_code': ('hk-bus-crawling', 'master', 'crawling/mergeRoutes.py'),
    'database_readme': ('hk-bus-crawling', 'master', 'README.md'),
    'database_license': ('hk-bus-crawling', 'master', 'LICENSE'),
    'geometry_code': ('route-waypoints', 'main', 'waypoints.py'),
    'geometry_readme': ('route-waypoints', 'main', 'README.md'),
    'geometry_license': ('route-waypoints', 'main', 'LICENSE'),
    'rail_geometry_sample': ('route-waypoints', 'main', 'mtr/isl.json'),
    'light_rail_geometry_sample': ('route-waypoints', 'main', 'lrt/610_O.json'),
    'ferry_geometry_sample': ('route-waypoints', 'main', 'ferry/7021.json'),
}
# The Long Win API is part of KMB's official inventory. No arbitrary aliases.
ALIASES = {'LWB': 'KMB', 'NWFB': 'CTB', 'LRTFEEDER': 'LRTFEEDER'}
POLICY = {'version': 1, 'max_stop_offset_m': 50, 'max_mean_offset_m': 20,
          'name_corroborated_stop_offset_m': 100, 'name_corroborated_mean_offset_m': 30,
          'sequence': 'equal_length_full_order_preserving_repeated_visits',
          'ambiguous_service_variants': 'reject',
          'operator_current_sequence_conflict': 'reject_hkbus_fallback',
          'coordinates': 'evidence_only_do_not_relocate_gtfs_stops'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + '.part')
    part.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
    part.replace(path)


def fetch(root, offline=False, refresh=False):
    """Cache commit-pinned originals plus content hashes; no silent cache repair."""
    import requests
    base = Path(root) / 'data/transit_enrichment/raw/identity'
    base.mkdir(parents=True, exist_ok=True)
    mp = base / 'manifest.json'
    manifest = json.loads(mp.read_text()) if mp.exists() else {'sources': {}}
    previous = manifest.get('sources', {})
    revisions = {}
    result = {}
    for name, (repo, branch, filename) in SOURCES.items():
        local_name = 'routeFareList.min.json' if name == 'database' else repo + '-' + Path(filename).name
        if local_name.endswith(('.py', '.md')) or Path(filename).name == 'LICENSE':
            local_name += '.txt'
        target = base / local_name
        entry = previous.get(name)
        if not refresh and entry and target.exists():
            if digest(target) != entry['sha256']:
                raise ValueError('Identity source checksum mismatch: ' + name)
            result[name] = entry
            continue
        if offline:
            raise ValueError('Missing verified identity source; fetch online first: ' + name)
        key = (repo, branch)
        if key not in revisions:
            response = requests.get(f'https://api.github.com/repos/hkbus/{repo}/commits/{branch}', timeout=60)
            response.raise_for_status()
            revisions[key] = response.json()
        commit = revisions[key]
        revision = commit['sha']
        url = f'https://raw.githubusercontent.com/hkbus/{repo}/{revision}/{filename}'
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        if name == 'database':
            data = response.json()
            if not isinstance(data.get('routeList'), dict) or not isinstance(data.get('stopList'), dict):
                raise ValueError('Unsupported hkbus database schema')
        part = target.with_suffix('.part')
        part.write_bytes(response.content)
        part.replace(target)
        result[name] = dict(path=target.name, url=url, sha256=digest(target), commit=revision,
            branch=branch, source_path=filename, repository=f'https://github.com/hkbus/{repo}',
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            source_commit_at=commit.get('commit', {}).get('committer', {}).get('date'),
            credit='HK Bus Crawling@2021; original transport data retains its source terms',
            use='Data source evidence; source code archived for audit, not imported or executed')
        save(mp, {'sources': {**previous, **result}})
    manifest = {'sources': result}
    save(mp, manifest)
    return manifest


def operator(value):
    value = str(value).upper()
    return ALIASES.get(value, value)


def pattern_key(route_id, stop_ids):
    # Identical to surface_timing.pattern_for, independent of generated trip IDs.
    return hashlib.sha256(json.dumps([str(route_id), list(stop_ids)]).encode()).hexdigest()[:20]


def haversine(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    v = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(v)))


def _point(stop):
    try:
        lat, lon = float(stop.get('lat', stop.get('stop_lat'))), float(stop.get('lon', stop.get('stop_lon')))
        if math.isfinite(lat) and math.isfinite(lon) and 22 <= lat <= 23 and 113 <= lon <= 115:
            return lat, lon
    except (TypeError, ValueError):
        pass
    raise ValueError('Missing or invalid Hong Kong stop coordinates')


def _normalized_name(value):
    # Operator bay codes are identifiers appended to the same English stop name.
    # Do not remove meaningful text such as (NORTH), station exits or road names.
    value = re.sub(r"\s*\([A-Z]{1,3}[0-9]{2,5}\)\s*$", "", str(value).upper())
    return ''.join(c for c in unicodedata.normalize('NFKC', value) if c.isalnum())


def same_operator_stop_name(gtfs_stop, source_stop, op):
    """Exact operator-specific names only; no substring/fuzzy/location guessing."""
    if not op or not source_stop.get('name_en'):
        return False
    names = set()
    for section in str(gtfs_stop.get('stop_name', '')).split('|'):
        match = re.match(r"\[([^]]+)\]\s*(.*)", section)
        if not match or operator(op) not in {operator(v) for v in match[1].split('+')}:
            continue
        for value in re.split(r'<br\s*/?>|</br>', match[2], flags=re.I):
            names.add(_normalized_name(value))
    return _normalized_name(source_stop['name_en']) in names


def sequence_fit(gtfs_stops, source_stops, policy=POLICY, op=None):
    """No deduplication, reverse matching, stop skipping, or nearest-only join.

    A shared TD stop can be tens of metres from an operator's boarding bay.
    A larger coordinate tolerance is allowed only when its operator-specific
    English name also matches exactly (ignoring a trailing operator bay code).
    """
    if len(gtfs_stops) != len(source_stops) or len(gtfs_stops) < 2:
        return None, 'stop_count_mismatch'
    try:
        offsets = [haversine(_point(a), _point(b)) for a, b in zip(gtfs_stops, source_stops)]
    except ValueError:
        return None, 'invalid_stop_coordinates'
    mean = sum(offsets) / len(offsets)
    for a, b, distance in zip(gtfs_stops, source_stops, offsets):
        if distance > policy['max_stop_offset_m']:
            if distance > policy.get('name_corroborated_stop_offset_m', policy['max_stop_offset_m']) or not same_operator_stop_name(a, b, op):
                return None, 'ordered_stop_coordinate_mismatch'
    if mean > policy['max_mean_offset_m']:
        if mean > policy.get('name_corroborated_mean_offset_m', policy['max_mean_offset_m']):
            return None, 'ordered_stop_coordinate_mismatch'
        if any(distance > policy['max_mean_offset_m'] and not same_operator_stop_name(a, b, op)
               for a, b, distance in zip(gtfs_stops, source_stops, offsets)):
            return None, 'ordered_stop_coordinate_mismatch'
    return offsets, None


def patterns_from_tables(tables):
    """Tables can contain only bus/minibus trips, to avoid rail all-pairs memory."""
    def values(table):
        return table.values() if isinstance(table, dict) else table
    routes = {r['route_id']: r for r in values(tables['routes.txt']) if str(r['route_type']) == '3'}
    stops = {r['stop_id']: r for r in values(tables['stops.txt'])}
    trips = {t['trip_id']: t for t in values(tables['trips.txt']) if t['route_id'] in routes}
    stop_times = defaultdict(list)
    for row in values(tables['stop_times.txt']):
        if row['trip_id'] in trips:
            stop_times[row['trip_id']].append(row)
    patterns = {}
    for tid, rows in stop_times.items():
        rows.sort(key=lambda x: int(x['stop_sequence']))
        trip = trips[tid]
        ids = [x['stop_id'] for x in rows]
        key = pattern_key(trip['route_id'], ids)
        if key not in patterns:
            patterns[key] = dict(key=key, route_id=trip['route_id'], stop_ids=ids,
                direction_ids=[], trip_count=0)
        patterns[key]['trip_count'] += 1
        direction = str(trip.get('direction_id', ''))
        if direction and direction not in patterns[key]['direction_ids']:
            patterns[key]['direction_ids'].append(direction)
    return {'routes': routes, 'stops': stops, 'patterns': patterns}


def read_patterns(feed):
    """Stream the large stop_times file; only surface trip metadata is retained."""
    with zipfile.ZipFile(feed) as z:
        def read(name):
            return csv.DictReader(io.TextIOWrapper(z.open(name), encoding='utf-8-sig'))
        routes = {r['route_id']: r for r in read('routes.txt') if r['route_type'] == '3'}
        stops = {r['stop_id']: r for r in read('stops.txt')}
        trips = {t['trip_id']: t for t in read('trips.txt') if t['route_id'] in routes}
        patterns, seen = {}, set()
        for tid, group in groupby(read('stop_times.txt'), lambda r: r['trip_id']):
            if tid not in trips:
                continue  # groupby advances without storing non-surface rows.
            if tid in seen:
                raise ValueError('Non-contiguous GTFS stop times: ' + tid)
            seen.add(tid)
            rows = sorted(group, key=lambda r: int(r['stop_sequence']))
            trip = trips[tid]
            ids = [r['stop_id'] for r in rows]
            key = pattern_key(trip['route_id'], ids)
            item = patterns.setdefault(key, dict(key=key, route_id=trip['route_id'], stop_ids=ids,
                direction_ids=[], trip_count=0))
            item['trip_count'] += 1
            direction = str(trip.get('direction_id', ''))
            if direction and direction not in item['direction_ids']:
                item['direction_ids'].append(direction)
    return {'routes': routes, 'stops': stops, 'patterns': patterns}


def hkbus_patterns(database):
    """Normalize each operator separately; do not collapse jointly run buses."""
    out = []
    for key, route in database.get('routeList', {}).items():
        if not route.get('gtfsId'):
            continue
        for co, ids in route.get('stops', {}).items():
            stops = []
            for seq, sid in enumerate(ids, 1):
                s = database.get('stopList', {}).get(sid, {})
                location = s.get('location', {})
                stops.append(dict(native_stop_id=str(sid), stop_id=f'{operator(co)}:{sid}', seq=seq,
                    lat=location.get('lat'), lon=location.get('lng'),
                    name_en=s.get('name', {}).get('en'), name_tc=s.get('name', {}).get('zh')))
            out.append(dict(key=key + '|' + co, hkbus_key=key, operator=operator(co), route=str(route.get('route', '')).upper(),
                direction=route.get('bound', {}).get(co), service_type=str(route.get('serviceType', '')),
                gtfs_id=str(route['gtfsId']), stops=stops, native_stop_ids=[str(s) for s in ids],
                source_url=REPOSITORY))
    return out



def closed_loop_candidates(current, route, pattern):
    """Prove a complete CTB circular path from overlapping operator halves.

    Some Citybus API directions overlap (N796 is35+45 visits with17 shared),
    rather than representing two disjoint halves. Only an explicit closed GTFS
    circular pattern can use this proof. Exact multi-stop overlap is mandatory,
    and the resulting full sequence still passes the normal coordinate guards.
    No overlap means no inferred connection. Source direction/sequence remain
    on each visit for safe live ETA requests.
    """
    ids = pattern['stop_ids']
    if not ids or ids[0] != ids[-1] or 'CIRCULAR' not in route.get('route_long_name', '').upper():
        return []
    if len(current) != 2 or {p.get('direction') for p in current} != {'O', 'I'} or any(operator(p['operator']) != 'CTB' for p in current):
        return []
    result = []
    for a, b in (current, current[::-1]):
        x = [str(s['native_stop_id']) for s in a['stops']]
        y = [str(s['native_stop_id']) for s in b['stops']]
        if not x or not y or x[0] != y[-1]:
            continue
        overlaps = [n for n in range(2, min(len(x), len(y))) if x[-n:] == y[:n]]
        if len(overlaps) != 1:
            continue
        overlap = overlaps[0]
        visits = []
        for source, source_visits in ((a, a['stops']), (b, b['stops'][overlap:])):
            for visit in source_visits:
                visits.append(dict(visit, official_direction=source['direction'],
                    official_sequence=visit.get('seq'), official_pattern_key=source['key']))
        if len(visits) != len(ids):
            continue
        result.append(dict(a, key=a['key'] + '+' + b['key'], direction=a['direction'] + b['direction'],
            stops=visits, native_stop_ids=[str(v['native_stop_id']) for v in visits],
            official_pattern_keys=[a['key'], b['key']],
            circular_proof=dict(method='exact_operator_direction_overlap', shared_stop_visits=overlap,
                component_pattern_keys=[a['key'], b['key']],
                warning='Operator direction labels describe overlapping portions. Each visit retains its own source direction and sequence.'),
            source_files=sorted(set(a.get('source_files', []) + b.get('source_files', [])))))
    return result

def _signature(candidate):
    return (operator(candidate['operator']), str(candidate.get('direction', '')),
        str(candidate.get('service_type') or ''), str(candidate.get('route_id') or ''),
        tuple(str(s['native_stop_id']) for s in candidate['stops']))


def _matched(pattern, candidate, offsets, tier, hkbus_keys=None, policy=POLICY):
    op = operator(candidate['operator'])
    visits = [dict(seq=i, gtfs_stop_id=sid, native_stop_id=str(s['native_stop_id']),
        stop_id=f"{op}:{s['native_stop_id']}", lat=float(s['lat']), lon=float(s['lon']),
        offset_m=round(offset, 2), name_en=s.get('name_en'), name_tc=s.get('name_tc'),
        exact_operator_name_corroboration=(offset > policy['max_stop_offset_m'] or
            (sum(offsets) / len(offsets) > policy['max_mean_offset_m'] and offset > policy['max_mean_offset_m'])),
        official_direction=s.get('official_direction', candidate.get('direction')) if tier == 'official_full_stop_sequence' else None,
        official_sequence=s.get('official_sequence', s.get('seq')) if tier == 'official_full_stop_sequence' else None,
        official_pattern_key=s.get('official_pattern_key', candidate['key']) if tier == 'official_full_stop_sequence' else None)
        for i, (sid, s, offset) in enumerate(zip(pattern['stop_ids'], candidate['stops'], offsets), 1)]
    return dict(operator=op, route=str(candidate['route']).upper(), direction=candidate.get('direction'),
        service_type=candidate.get('service_type'), native_route_id=candidate.get('route_id'),
        official_pattern_key=candidate['key'] if tier == 'official_full_stop_sequence' else None,
        official_pattern_keys=candidate.get('official_pattern_keys', [candidate['key']]) if tier == 'official_full_stop_sequence' else [],
        circular_proof=candidate.get('circular_proof'),
        hkbus_keys=hkbus_keys or ([candidate['hkbus_key']] if candidate.get('hkbus_key') else []),
        evidence=tier, stop_visits=visits, native_stop_ids=[s['native_stop_id'] for s in visits],
        max_stop_offset_m=round(max(offsets), 2), mean_stop_offset_m=round(sum(offsets) / len(offsets), 2),
        source_url=candidate.get('source_url') or candidate.get('url') or candidate.get('source_urls'),
        source_files=candidate.get('source_files'))


def match_patterns(tables, database, official_patterns=None, policy=POLICY):
    """Return pattern-key crosswalk with accepted matches and rejection reasons.

    `tables` is read_patterns() output or GTFS tables. `official_patterns` is
    official.normalize(root)'s dict or a list of normalized operator patterns.
    Official availability without a compatible sequence blocks an older hkbus
    fallback. A pattern can retain multiple operators, but never two uncertain
    variants of one operator. Ambiguous candidates are not ranked by proximity.
    """
    data = tables if 'patterns' in tables and 'routes' in tables else patterns_from_tables(tables)
    officials = (official_patterns or {}).get('patterns', []) if isinstance(official_patterns, dict) else (official_patterns or [])
    official_index, hk_index = defaultdict(list), defaultdict(list)
    available_routes = {(operator(p['operator']), str(p['route']).upper())
        for p in (official_patterns or {}).get('available_routes', [])} if isinstance(official_patterns, dict) else set()
    available_operators = {op for op, number in available_routes}
    for p in officials:
        official_index[(operator(p['operator']), str(p['route']).upper())].append(p)
    for p in hkbus_patterns(database):
        hk_index[(p['gtfs_id'], p['operator'], p['route'])].append(p)
    result, stats, reasons = {}, Counter(), Counter()
    for key, pattern in data['patterns'].items():
        route = data['routes'][pattern['route_id']]
        number = route['route_short_name'].upper()
        ops = sorted({operator(op) for op in route['agency_id'].split('+')})
        gtfs_stops = [data['stops'][sid] for sid in pattern['stop_ids']]
        matches, rejections = [], []
        for op in ops:
            current = official_index[(op, number)]
            absent_current_route = op in available_operators and (op, number) not in available_routes
            has_official = bool(current) or op in available_operators
            candidates = (current + closed_loop_candidates(current, route, pattern)) if has_official else hk_index[(pattern['route_id'], op, number)]
            tier = 'official_full_stop_sequence' if has_official else 'hkbus_full_stop_sequence'
            fit = {}
            errors = Counter()
            for candidate in candidates:
                offsets, error = sequence_fit(gtfs_stops, candidate['stops'], policy, op)
                if error:
                    errors[error] += 1
                    continue
                signature = _signature(candidate)
                fit[signature] = (candidate, offsets)
            if len(fit) == 1:
                candidate, offsets = next(iter(fit.values()))
                corroborating = []
                if current:
                    for h in hk_index[(pattern['route_id'], op, number)]:
                        if tuple(h['native_stop_ids']) == tuple(str(s['native_stop_id']) for s in candidate['stops']):
                            # hkbus service_type is not comparable for CTB/GMB (official exposes no such value).
                            if op == 'KMB' and str(h['service_type']) != str(candidate.get('service_type')):
                                continue
                            if op == 'KMB' and h['direction'] != candidate.get('direction'):
                                continue
                            corroborating.append(h['hkbus_key'])
                matches.append(_matched(pattern, candidate, offsets, tier, sorted(corroborating), policy))
                stats[tier + '_matches'] += 1
            else:
                reason = 'ambiguous_service_variants' if len(fit) > 1 else ('absent_from_complete_official_inventory' if absent_current_route else 'official_sequence_conflict' if has_official else 'no_verified_hkbus_sequence' if candidates else 'no_identity_candidate')
                rejections.append(dict(operator=op, reason=reason, candidates=len(candidates),
                    qualifying_candidates=len(fit), candidate_failures=dict(errors)))
                reasons[reason] += 1
        result[key] = dict(pattern, route_name=number, agency_id=route['agency_id'], matches=matches,
            status='matched' if matches else 'rejected', rejections=rejections)
        stats['patterns'] += 1
        stats['matched_patterns' if matches else 'rejected_patterns'] += 1
        stats['matched_stop_visits'] += sum(len(m['stop_visits']) for m in matches)
    return {'policy': policy, 'statistics': dict(stats), 'rejection_counts': dict(reasons), 'patterns': result}



def pair_key(op, start, end):
    return f'{operator(op)}:{start}>{end}'


def geometry_conflicts(feed, crosswalk, surface_proof=None):
    """Veto shared native stop pairs whose validated CSDI paths disagree.

    Historical timing records have no route identity. The same consecutive
    operator stop pair may use different roads or appear twice around a loop.
    Missing geometry for a shared pair is also returned as a veto: lack of a
    known conflict is not affirmative proof of the same route segment.
    Only validated CSDI shapes are compared, never straight-line fallbacks.
    """
    from bisect import bisect_left, bisect_right
    from pyproj import Transformer
    from shapely.geometry import LineString
    transform = Transformer.from_crs('EPSG:4326', 'EPSG:2326', always_xy=True)
    policy = dict(max_hausdorff_m=60, absolute_length_difference_m=100,
        relative_length_difference=0.2, missing_shared_geometry='veto')
    with zipfile.ZipFile(feed) as z:
        if surface_proof is None:
            if 'surface_timing_provenance.json' not in z.namelist():
                raise ValueError('Geometry audit needs a feed with validated surface provenance')
            surface_proof = json.loads(z.read('surface_timing_provenance.json'))
        source_patterns = surface_proof['patterns']
        pairs = defaultdict(list)
        for key, pattern in crosswalk['patterns'].items():
            source = source_patterns.get(key, {})
            valid = source.get('kind') == 'csdi_route_distance'
            distances = source.get('distances_m', []) if valid else []
            for match in pattern.get('matches', []):
                ids = match['native_stop_ids']
                if valid and len(distances) != len(ids):
                    raise ValueError('Crosswalk/CSDI stop-visit count differs: ' + key)
                for i, (start, end) in enumerate(zip(ids, ids[1:])):
                    pairs[pair_key(match['operator'], start, end)].append(dict(pattern_key=key,
                        route_id=pattern['route_id'], visit=i, shape_id=source.get('shape_id') if valid else None,
                        start_m=distances[i] if valid else None, end_m=distances[i+1] if valid else None))
        shared = {key: visits for key, visits in pairs.items() if len(visits) > 1}
        wanted = {v['shape_id'] for visits in shared.values() for v in visits if v['shape_id']}
        shapes = defaultdict(list)
        if wanted and 'shapes.txt' in z.namelist():
            reader = csv.DictReader(io.TextIOWrapper(z.open('shapes.txt'), encoding='utf-8-sig'))
            for row in reader:
                if row['shape_id'] in wanted:
                    shapes[row['shape_id']].append((int(row['shape_pt_sequence']),
                        float(row['shape_dist_traveled']),
                        transform.transform(float(row['shape_pt_lon']), float(row['shape_pt_lat']))))
    prepared = {}
    for sid, rows in shapes.items():
        rows.sort()
        prepared[sid] = ([r[1] for r in rows], [r[2] for r in rows])
    def segment(visit):
        distances, points = prepared[visit['shape_id']]
        def at(value):
            index = min(len(distances) - 2, max(0, bisect_right(distances, value) - 1))
            span = distances[index+1] - distances[index]
            fraction = min(1, max(0, (value - distances[index]) / span)) if span else 0
            return tuple(points[index][j] + fraction * (points[index+1][j] - points[index][j]) for j in (0, 1))
        first, last = visit['start_m'], visit['end_m']
        interior = points[bisect_right(distances, first):bisect_left(distances, last)]
        return LineString([at(first), *interior, at(last)])
    vetoes = {}
    for key, visits in shared.items():
        reason = None
        if any(not v['shape_id'] or v['shape_id'] not in prepared for v in visits):
            reason = 'shared_pair_has_unverified_geometry'
        else:
            lengths = [v['end_m'] - v['start_m'] for v in visits]
            if max(lengths) - min(lengths) > max(policy['absolute_length_difference_m'], policy['relative_length_difference'] * min(lengths)):
                reason = 'different_segment_lengths'
            else:
                reference = segment(visits[0])
                if any(reference.hausdorff_distance(segment(v)) > policy['max_hausdorff_m'] for v in visits[1:]):
                    reason = 'different_segment_alignments'
        if reason:
            vetoes[key] = dict(reason=reason, visits=visits)
    return dict(policy=policy, statistics=dict(native_pairs=len(pairs), shared_native_pairs=len(shared),
        vetoed_pairs=len(vetoes), reasons=dict(Counter(v['reason'] for v in vetoes.values()))),
        vetoes=vetoes)

def build(root, feed, official=None, output=None, offline=True):
    root = Path(root)
    manifest = fetch(root, offline=offline)
    database = json.loads((root / 'data/transit_enrichment/raw/identity/routeFareList.min.json').read_text())
    if official is None:
        path = root / 'data/transit_enrichment/official.json'
        if path.exists():
            from transit_enrichment.official import load
            official = load(root)  # Verify/re-normalize source bytes, not a mutable derived JSON.
        else:
            official = None
    result = match_patterns(read_patterns(feed), database, official)
    result.update(input_sha256=digest(feed), source_manifest=manifest,
        official_source_manifest=(official or {}).get('source_manifest', {}) if isinstance(official, dict) else {})
    save(output or root / 'data/transit_enrichment/identity.json', result)
    return result


if __name__ == '__main__':
    # Direct file execution has this package directory, not scripts/, on sys.path.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['fetch', 'build'])
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.command == 'fetch':
        print(json.dumps(fetch(args.root, args.offline, args.refresh), indent=2))
    else:
        result = build(args.root, args.input or args.root / 'data/generated/hk-transit-EXPERIMENTAL.gtfs.zip',
            output=args.output, offline=args.offline)
        print(json.dumps({'statistics': result['statistics'], 'rejection_counts': result['rejection_counts']}, indent=2))
