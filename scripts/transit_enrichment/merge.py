"""Compile validated source additions, preserving original timing anchors."""
import csv
import hashlib
import io
import json
import shutil
from collections import Counter, defaultdict
from datetime import date, timedelta
from itertools import chain
from pathlib import Path
import zipfile
from .feed import rows, write_rows, surface_tables, index_patterns, seconds, clock, save, sha
from . import identity, official, history, holidays, timing, published

PROOF = 'transit_enrichment_provenance.json'


def copy_member(src, dst, name):
    with src.open(name) as a, dst.open(name, 'w') as b:
        shutil.copyfileobj(a, b, 1024 * 1024)


def fields(src, name, defaults):
    reader = rows(src, name)
    return reader.fieldnames if hasattr(reader, 'fieldnames') else defaults


def profile(stop_times):
    origin = seconds(stop_times[0]['departure_time'])
    return tuple(tuple((k, seconds(v) - origin if k in ('arrival_time', 'departure_time') and v else v)
                       for k, v in sorted(r.items()) if k not in ('trip_id', 'shape_dist_traveled')) for r in stop_times)


def prepare_schedules(tables, crosswalk, official_data, start, end, holiday_data):
    from .schedule import compile_schedule
    trips, trip_times, patterns = index_patterns(tables)
    sources = {p['key']: p for p in official_data['patterns']}
    plans, rejected = {}, {}
    for key, pattern in sorted(patterns.items()):
        matches = crosswalk['patterns'].get(key, {}).get('matches', [])
        candidates = [m for m in matches if m['operator'] == 'GMB' and m['evidence'] == 'official_full_stop_sequence']
        if len(candidates) != 1 or len(matches) != 1:
            continue
        source = sources[candidates[0]['official_pattern_key']]
        compiled = compile_schedule(source, start, end, holiday_data['dates'], holiday_data['years'])
        if not compiled['usable']:
            rejected[key] = compiled['reason']; continue
        tids = pattern['trip_ids']
        try:
            profiles = {profile(trip_times[t]) for t in tids}
        except (ValueError, KeyError):
            rejected[key] = 'missing_source_timing_anchors'; continue
        if len(profiles) != 1:
            rejected[key] = 'different_published_running_profiles_for_same_stops'; continue
        template = min(tids)
        plans[key] = dict(source=source, schedule=compiled, template=template,
                          trips=tids, rows=trip_times[template], trip=trips[template])
    return plans, rejected


def compile_base(root, source, output, start, end):
    """Replace only safe GMB schedules inside the explicit build window."""
    root, source, output = Path(root), Path(source), Path(output)
    data = official.normalize(root)
    hol = holidays.load(root)
    manifest = identity.fetch(root, offline=True)
    db = json.loads((root / 'data/transit_enrichment/raw/identity/routeFareList.min.json').read_text())
    with zipfile.ZipFile(source) as z:
        if PROOF in z.namelist():
            raise ValueError('Input is already enriched; rebuild from the indoor/base stage')
        tables = surface_tables(z)
        crosswalk = identity.match_patterns({k + '.txt': v for k, v in tables.items()}, db, data)
        plans, rejected = prepare_schedules(tables, crosswalk, data, start, end, hol)
        # Do not widen a restricted original service calendar on the strength of
        # an API that may omit school/event notes. Conflicts retain the old feed.
        calendars_by_id = {r['service_id']: r for r in rows(z, 'calendar.txt')}
        exceptions = {(r['service_id'], r['date']): r['exception_type'] for r in rows(z, 'calendar_dates.txt')}
        trip_services = {t['trip_id']: t['service_id'] for t in tables['trips']}
        weekdays = ('monday','tuesday','wednesday','thursday','friday','saturday','sunday')
        def active(sid, d):
            if (sid,d) in exceptions: return exceptions[sid,d] == '1'
            r = calendars_by_id.get(sid, {})
            when = __import__('datetime').datetime.strptime(d, '%Y%m%d').date()
            return r.get('start_date', '99999999') <= d <= r.get('end_date', '00000000') and r.get(weekdays[when.weekday()]) == '1'
        for key, plan in list(plans.items()):
            if any((d['departures'] or d['frequencies']) and not any(active(trip_services[t], d['date']) for t in plan['trips']) for d in plan['schedule']['days']):
                rejected[key] = 'official_day_rule_conflicts_with_existing_restricted_calendar'
                del plans[key]
        trips, trip_times, patterns = index_patterns(tables)
        trip_pattern = {tid: key for key, p in patterns.items() for tid in p['trip_ids']}
        proof = dict(format=1, window=[str(start), str(end)], identities=crosswalk,
                     trips=trip_pattern, timetables={}, history={},
                     sources=dict(identity=manifest, official=data['source_manifest'], holidays=hol['source']),
                     policy='Verified full ordered stop sequences. Original supplied timing anchors are retained. Native coordinates are evidence, not automatic shared-stop replacements.')
        changed, new_trips, new_times, new_freq, new_dates = {}, [], [], [], []
        day_strings = [(start + timedelta(days=i)).strftime('%Y%m%d') for i in range((end-start).days+1)]
        for key, plan in sorted(plans.items()):
            old = plan['trip']; origin = seconds(plan['rows'][0]['departure_time'])
            # One service calendar per distinct set of official bands; no duplicated daily trips.
            groups = defaultdict(list)
            for day in plan['schedule']['days']:
                if day['departures'] or day['frequencies']:
                    groups[json.dumps([day['departures'], day['frequencies']], sort_keys=True)].append(day['date'])
            if not groups:
                rejected[key] = 'no_departures_in_build_window'; continue
            for tid in plan['trips']:
                sid = trips[tid]['service_id']
                changed[tid] = 'ENR:RETAIN:' + hashlib.sha256(sid.encode()).hexdigest()[:16]
            for encoded, dates in sorted(groups.items()):
                departures, freqs = json.loads(encoded)
                suffix = hashlib.sha256((key + encoded).encode()).hexdigest()[:20]
                service_id = 'ENR:GMB:' + suffix
                new_dates.extend(dict(service_id=service_id, date=d, exception_type='1') for d in dates)
                bands = [('departure', dict(start_seconds=t)) for t in departures] + [('frequency', f) for f in freqs]
                for n, (kind, band) in enumerate(bands):
                    tid = service_id + ':' + str(n)
                    new_trips.append(dict(old, service_id=service_id, trip_id=tid))
                    offset = band['start_seconds'] - origin
                    for row in plan['rows']:
                        new_times.append(dict(row, trip_id=tid,
                            arrival_time=clock(seconds(row['arrival_time']) + offset) if row['arrival_time'] else '',
                            departure_time=clock(seconds(row['departure_time']) + offset) if row['departure_time'] else ''))
                    if kind == 'frequency':
                        new_freq.append(dict(trip_id=tid, start_time=clock(band['start_seconds']),
                            end_time=clock(band['end_seconds']), headway_secs=str(band['headway_secs']), exact_times='0'))
                    proof['trips'][tid] = key
                    proof['timetables'][tid] = dict(kind=kind, band=band,
                        official_pattern_key=plan['source']['key'], source_url=plan['source']['source_url'],
                        snapshot=plan['source'].get('retrieved_at', '')[:10],
                        original_trip=plan['template'], source_files=plan['source']['source_files'],
                        warnings=plan['schedule']['warnings'], policy=plan['schedule']['policy'])
        retained = {trips[tid]['service_id']: sid for tid, sid in changed.items()}
        calendars = list(rows(z, 'calendar.txt'))
        dates = list(rows(z, 'calendar_dates.txt'))
        calendars += [dict(r, service_id=retained[r['service_id']]) for r in calendars if r['service_id'] in retained]
        dates += [dict(r, service_id=retained[r['service_id']]) for r in dates
                  if r['service_id'] in retained and r['date'] not in day_strings]
        dates += [dict(service_id=sid, date=d, exception_type='2') for sid in retained.values() for d in day_strings]
        proof['statistics'] = dict(identity=crosswalk['statistics'], official=data['summary'],
            gmb_patterns_applied=len({proof['trips'][tid] for tid in proof['timetables']}),
            gmb_patterns_retained=len(rejected), official_timetable_trips=len(new_trips),
            official_frequency_bands=len(new_freq), official_departure_trips=len(new_trips)-len(new_freq),
            native_coordinates_verified=len({v['stop_id'] for p in crosswalk['patterns'].values() for m in p['matches'] for v in m['stop_visits']}))
        proof['timetable_rejections'] = rejected
        output.parent.mkdir(parents=True, exist_ok=True)
        tmp = output.with_suffix('.part')
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as out:
            for name in z.namelist():
                if name in ('trips.txt', 'stop_times.txt', 'frequencies.txt', 'calendar.txt', 'calendar_dates.txt', PROOF):
                    continue
                copy_member(z, out, name)
            write_rows(out, 'trips.txt', fields(z, 'trips.txt', []), chain(
                (dict(t, service_id=changed[t['trip_id']]) if t['trip_id'] in changed else t for t in rows(z, 'trips.txt')), new_trips))
            write_rows(out, 'stop_times.txt', fields(z, 'stop_times.txt', []), chain(rows(z, 'stop_times.txt'), new_times))
            write_rows(out, 'frequencies.txt', fields(z, 'frequencies.txt', ['trip_id','start_time','end_time','headway_secs','exact_times']), chain(rows(z, 'frequencies.txt'), new_freq))
            write_rows(out, 'calendar.txt', fields(z, 'calendar.txt', []), calendars)
            write_rows(out, 'calendar_dates.txt', fields(z, 'calendar_dates.txt', ['service_id','date','exception_type']), chain(dates, new_dates))
            out.writestr(PROOF, json.dumps(proof, ensure_ascii=False))
        tmp.replace(output)
    from .check import verify_base
    verify_base(source, output, root / 'data/transit_enrichment/base_validation.json')
    save(root / 'data/transit_enrichment/base_report.json', dict(statistics=proof['statistics'], rejected=rejected,
         source_sha256=sha(source), output_sha256=sha(output)))
    print(json.dumps(proof['statistics']), flush=True)
    return proof


def historical_match(pattern):
    """A joint service cannot inherit one operator's anonymous timing history."""
    matches = pattern.get('matches', [])
    operators = {identity.operator(op) for op in pattern.get('agency_id', '').split('+')}
    if len(matches) != 1 or operators != {matches[0]['operator']}:
        return None
    return matches[0]


def compile_timing(root, anchors, source, output):
    root, output = Path(root), Path(output)
    hist = history.normalize(root)
    published_data = published.normalize(root)
    with zipfile.ZipFile(anchors) as a, zipfile.ZipFile(source) as z:
        _, original, _ = index_patterns(surface_tables(a))
        _, current, _ = index_patterns(surface_tables(z))
        proof = json.loads(z.read(PROOF))
        surface = json.loads(z.read('surface_timing_provenance.json'))
        proof['published_route_metadata'] = {rid: value for rid,value in published_data['routes'].items() if rid in {p['route_id'] for p in proof['identities']['patterns'].values()}}
        proof['sources']['published_routes'] = published_data['source']
        proof['history_checks'] = {}
        audit = identity.geometry_conflicts(source, proof['identities'], surface)
        proof['geometry_conflicts'] = audit
        conflict_keys = set(audit['vetoes'])
        replacements, reasons, patterns_applied = {}, Counter(), set()
        for tid, key in sorted(proof['trips'].items()):
            if tid not in original or tid not in current: continue
            match = historical_match(proof['identities']['patterns'][key])
            geom = surface['patterns'].get(key, {})
            if match is None:
                reasons['ambiguous_or_missing_identity'] += 1; continue
            if geom.get('kind') != 'csdi_route_distance':
                reasons['no_verified_road_geometry'] += 1; continue
            candidate, accepted, rejected = timing.reweight(original[tid], current[tid], match['native_stop_ids'],
                hist['overall'], geom['distances_m'], {(a,b) for a,b in zip(match['native_stop_ids'], match['native_stop_ids'][1:]) if identity.pair_key(match['operator'], a, b) in conflict_keys})
            for r in rejected: reasons[r['reason']] += 1
            if rejected: proof['history_checks'][tid] = rejected
            if not accepted: continue
            replacements[tid] = candidate; patterns_applied.add(key)
            proof['history'][tid] = dict(pattern=key, spans=accepted, retained_spans=rejected,
                kind='eta_derived_proportions', source_url=hist['source']['source_url'], snapshot=hist['source']['snapshot'])
        proof['sources']['history'] = hist['source']
        proof['timing_policy'] = timing.POLICY
        proof['statistics'].update(history_source=hist['quality'], history_patterns_applied=len(patterns_applied),
            history_trips_applied=len(replacements), history_rows_reweighted=sum(
                not bool(original[t][i].get('arrival_time')) and r['arrival_time'] != current[t][i]['arrival_time']
                for t, rs in replacements.items() for i,r in enumerate(rs)),
            history_rejections=dict(reasons), conflicting_native_pairs=len(conflict_keys), hourly_profiles_collected=len(hist['hourly_files']))
        def updated():
            for tid, group in __import__('itertools').groupby(rows(z, 'stop_times.txt'), key=lambda r:r['trip_id']):
                if tid in replacements:
                    yield from replacements[tid]
                else:
                    yield from group
        tmp = output.with_suffix('.part')
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as out:
            for name in z.namelist():
                if name not in (PROOF, 'stop_times.txt'): copy_member(z, out, name)
            write_rows(out, 'stop_times.txt', fields(z, 'stop_times.txt', []), updated())
            out.writestr(PROOF, json.dumps(proof, ensure_ascii=False))
        tmp.replace(output)
    report = dict(statistics=proof['statistics'], timing_policy=timing.POLICY,
                  timetable_rejections=proof['timetable_rejections'],
                  anchors_sha256=sha(anchors), source_sha256=sha(source), output_sha256=sha(output))
    save(root / 'data/transit_enrichment/report.json', report)
    print(json.dumps(report['statistics']), flush=True)
    return report
