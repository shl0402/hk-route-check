#!/usr/bin/env python3
"""Semantic checks against immutable source: unchanged stops/times, calendars and provenance."""

import collections, json, zipfile
from datetime import datetime
from merge_gtfs import read_feed, GEN, WIKI, WEEK, sec


def active(sid, day, cal, ex):
	if day in ex.get(sid, {}):
		return ex[sid][day] == '1'
	c = cal.get(sid)
	return bool(
		c
		and c['start_date'] <= day <= c['end_date']
		and c[WEEK[datetime.strptime(day, '%Y%m%d').weekday()]] == '1'
	)


def check():
	base, _ = read_feed(GEN / 'hk-transit-PRE-WIKI.gtfs.zip')
	new, _ = read_feed(GEN / 'hk-transit-WIKI.gtfs.zip')
	report = json.loads((WIKI / 'merge_report.json').read_text())
	with zipfile.ZipFile(GEN / 'hk-transit-WIKI.gtfs.zip') as z:
		proof = json.loads(z.read('wiki_provenance.json'))['trips']
	for name in [
		'stops.txt',
		'routes.txt',
		'fare_rules.txt',
		'fare_attributes.txt',
		'transfers.txt',
	]:
		assert base.get(name) == new.get(name), f'Unexpected changes to {name}'
	assert [
		r for r in new['stop_times.txt'] if not r['trip_id'].startswith('WIKI:')
	] == base['stop_times.txt']
	trips = {r['trip_id']: r for r in new['trips.txt']}
	assert len(trips) == len(new['trips.txt'])
	assert set(proof) == {k for k in trips if k.startswith('WIKI:')}
	st = collections.defaultdict(list)
	for r in new['stop_times.txt']:
		st[r['trip_id']].append(r)
	for tid, p in proof.items():
		original = st[p['base_trip_id']]
		changed = st[tid]
		assert len(original) == len(changed)
		offset = sec(changed[0]['departure_time']) - sec(original[0]['departure_time'])
		for a, b in zip(original, changed):
			assert (
				a['stop_id'] == b['stop_id']
				and a['pickup_type'] == b['pickup_type']
				and a['drop_off_type'] == b['drop_off_type']
			)
			for col in ['arrival_time', 'departure_time']:
				assert (sec(b[col]) - sec(a[col]) == offset) if a[col] else b[col] == ''
	cal = {r['service_id']: r for r in new['calendar.txt']}
	ex = collections.defaultdict(dict)
	for r in new['calendar_dates.txt']:
		assert r['date'] not in ex[r['service_id']], 'Duplicate service/date exception'
		ex[r['service_id']][r['date']] = r['exception_type']
	original_groups = collections.defaultdict(list)
	wiki_groups = collections.defaultdict(list)
	for tr in new['trips.txt']:
		tid = tr['trip_id']
		if tid.startswith('WIKI:'):
			bits = tid.split(':')
			wiki_groups[(tr['route_id'], bits[2])].append(tr)
		elif '-' in tid:
			original_groups[(tr['route_id'], tid.split('-')[1])].append(tr)
	verified = 0
	for change in report['changes']:
		key = (change['route_id'], change['seq'])
		for d in change['dates']:
			assert not any(
				active(t['service_id'], d, cal, ex) for t in original_groups[key]
			), ('Duplicate government service', key, d)
			assert any(active(t['service_id'], d, cal, ex) for t in wiki_groups[key]), (
				'Missing replacement service',
				key,
				d,
			)
			verified += 1
	print(
		f'Semantic checks passed: {len(proof)} attributed trips; {verified} route/direction/date replacements; government stops, fares and running-time offsets preserved.'
	)


if __name__ == '__main__':
	check()
