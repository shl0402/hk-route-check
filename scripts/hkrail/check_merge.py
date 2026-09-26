#!/usr/bin/env python3
"""Independent GTFS integrity and retained-network/calendar checks for rail enrichment."""

import csv, io, json, zipfile
from collections import defaultdict
from datetime import datetime
from merge_gtfs import BASE, TARGET, WEEK, sec


def read(z, n):
	return list(csv.DictReader(io.TextIOWrapper(z.open(n), encoding='utf-8-sig')))


def active(sid, d, cal, ex):
	if d in ex[sid]:
		return ex[sid][d] == '1'
	c = cal.get(sid)
	return bool(
		c
		and c['start_date'] <= d <= c['end_date']
		and c[WEEK[datetime.strptime(d, '%Y%m%d').weekday()]] == '1'
	)


def check():
	with zipfile.ZipFile(BASE) as base, zipfile.ZipFile(TARGET) as new:
		proof = json.loads(new.read('rail_wiki_provenance.json'))
		changed = {c['original_trip'] for c in proof['changes'] if c['original_trip']}
		for n in [
			'stops.txt',
			'transfers.txt',
			'fare_rules.txt',
			'fare_attributes.txt',
			'wiki_provenance.json',
		]:
			assert base.read(n) == new.read(n), n
		br = read(base, 'routes.txt')
		nr = read(new, 'routes.txt')
		assert nr[: len(br)] == br
		btrips = read(base, 'trips.txt')
		ntrips = read(new, 'trips.txt')
		trips = {r['trip_id']: r for r in ntrips}
		assert len(trips) == len(ntrips)
		for t in btrips:
			actual = trips[t['trip_id']]
			assert (
				actual == dict(t, service_id=actual['service_id'])
				if t['trip_id'] in changed
				else actual == t
			)
		bf = read(base, 'frequencies.txt')
		nf = read(new, 'frequencies.txt')
		assert [r for r in nf if not r['trip_id'].startswith('RAILWIKI:')] == bf
		bbytes = base.read('stop_times.txt').rstrip(b'\r\n') + b'\n'
		assert new.read('stop_times.txt').startswith(bbytes)
		st = defaultdict(list)
		bst = defaultdict(list)
		for row in read(new, 'stop_times.txt'):
			if row['trip_id'] in proof['trips']:
				st[row['trip_id']].append(row)
			elif row['trip_id'].startswith('RAIL:'):
				bst[row['trip_id']].append(row)
		edges = set()
		for rows in bst.values():
			rows.sort(key=lambda r: int(r['stop_sequence']))
			for a, b in zip(rows, rows[1:]):
				edges.add(
					(
						a['stop_id'],
						b['stop_id'],
						sec(b['arrival_time']) - sec(a['departure_time']),
					)
				)
		for tid, p in proof['trips'].items():
			rows = st[tid]
			assert len(rows) >= 2
			# Every directed segment and duration must exist in the original input templates,
			# including joined loops, LOHAS through trains and independently sliced short workings.
			for a, b in zip(rows, rows[1:]):
				assert a['stop_id'] != b['stop_id'], tid
				assert (
					a['stop_id'],
					b['stop_id'],
					sec(b['arrival_time']) - sec(a['departure_time']),
				) in edges, (tid, a, b)
			if p['line'] in ['705', '706']:
				assert rows[0]['stop_id'] == rows[-1]['stop_id'] and len(rows) == 16
			if p['line'] == 'EAL':
				assert all(':RAC:' not in x['stop_id'] for x in rows)
		cal = {c['service_id']: c for c in read(new, 'calendar.txt')}
		ex = defaultdict(dict)
		for r in read(new, 'calendar_dates.txt'):
			assert r['date'] not in ex[r['service_id']]
			ex[r['service_id']][r['date']] = r['exception_type']
		checked = 0
		for c in proof['changes']:
			for d in c['dates']:
				if c['original_trip']:
					assert not active(
						trips[c['original_trip']]['service_id'], d, cal, ex
					)
				assert all(
					active(trips[tid]['service_id'], d, cal, ex)
					for tid in c['new_trip_ids']
				)
				checked += 1
		# No new exact duplicate path/departure on the same date.
		schedules = set()
		freq = {r['trip_id']: r for r in nf}
		for tid, p in proof['trips'].items():
			for d, v in ex[trips[tid]['service_id']].items():
				if v != '1':
					continue
				route = trips[tid]['route_id']
				path = tuple(r['stop_id'] for r in st[tid])
				f = freq.get(tid)
				timing = (
					tuple(f[k] for k in ['start_time', 'end_time', 'headway_secs'])
					if f
					else st[tid][0]['departure_time']
				)
				sig = (route, path, d, timing)
				assert sig not in schedules, (tid, d)
				schedules.add(sig)
		attrib = {r['trip_id'] for r in read(new, 'attributions.txt')}
		assert set(proof['trips']) <= attrib
		assert (
			len({p['line'] for p in proof['trips'].values() if p['system'] == 'MTR'})
			== 10
		)
		assert (
			len({p['line'] for p in proof['trips'].values() if p['system'] == 'LRT'})
			>= 11
		)
	print(
		f'PASS: {len(proof["trips"])} rail records; {checked} calendar checks; every segment supported by the baseline; bus data preserved.'
	)


if __name__ == '__main__':
	check()
