#!/usr/bin/env python3
"""Apply only line/direction-matched operator-derived estimates to a NEW GTFS.
The input is the unmodified rail-wiki feed. No timetable, fare, transfer or
coordinate assumptions are silently replaced using whole-journey API evidence.
"""

import argparse, csv, hashlib, io, json, pathlib, zipfile, collections
from mtr_api_patterns import MtrApiPatterns


def sec(v):
	h, m, s = map(int, v.split(':'))
	return h * 3600 + m * 60 + s


def clock(v):
	return f'{v//3600:02}:{v//60%60:02}:{v%60:02}'


def edge(route, a, b):
	system, line = route.split(':')[1:]
	return f'{system}:{line}:{int(a.split(":")[2])}>{int(b.split(":")[2])}'


def main():
	p = argparse.ArgumentParser(description=__doc__)
	p.add_argument('--root', type=pathlib.Path, required=True)
	a = p.parse_args()
	out = a.root / 'data/mtr_api'
	g = a.root / 'data/generated'
	src = g / 'hk-transit-RAIL-WIKI.gtfs.zip'
	dst = g / 'hk-transit-MTR-API.gtfs.zip'
	evidence = json.loads((out / 'normalized.json').read_text())
	segments = evidence['segments']
	used = set()
	all_edges = set()
	changed = 0
	trips_changed = set()
	origins = {}
	tails = {}
	last = {}
	proof = {
		'policy': 'MTR uses a complete cached API path matched by line, direction and ordered station IDs. No MTR distance fallback. Light Rail retains the separately labelled segment policy.',
		'semantics': 'MTR: complete line/path-matched API cumulative journey times, with no distance fallback. Light Rail: existing independently derived segment policy. Timetables remain wiki/government sourced.',
		'segments': {},
		'trips': {},
		'input_sha256': hashlib.sha256(src.read_bytes()).hexdigest(),
	}
	with zipfile.ZipFile(src) as source, zipfile.ZipFile(
		dst.with_suffix('.tmp'), 'w', zipfile.ZIP_DEFLATED
	) as target:
		trips = {
			r['trip_id']: r
			for r in csv.DictReader(
				io.TextIOWrapper(source.open('trips.txt'), encoding='utf-8-sig')
			)
		}
		hr_rows = collections.defaultdict(list)
		for r in csv.DictReader(
			io.TextIOWrapper(source.open('stop_times.txt'), encoding='utf-8-sig')
		):
			if trips[r['trip_id']]['route_id'].startswith('RAIL:MTR:'):
				hr_rows[r['trip_id']].append(r)
		api = MtrApiPatterns(a.root)
		hr_timing = {}
		for tid, rows in hr_rows.items():
			rows.sort(key=lambda r: int(r['stop_sequence']))
			ids = tuple(r['stop_id'].split(':')[2] for r in rows)
			timing = api.timing(trips[tid]['route_id'].split(':')[-1], ids)
			hr_timing[tid] = timing
			proof['trips'][tid] = timing
		for name in source.namelist():
			if name in ('stop_times.txt', 'mtr_api_provenance.json'):
				continue
			# Preserve all original timetable provenance and supplemental evidence.
			with source.open(name) as f, target.open(name, 'w') as t:
				import shutil

				shutil.copyfileobj(f, t)
		with source.open('stop_times.txt') as f, target.open(
			'stop_times.txt', 'w'
		) as t:
			reader = csv.DictReader(io.TextIOWrapper(f, encoding='utf-8-sig'))
			stream = io.TextIOWrapper(t, encoding='utf-8', newline='')
			writer = csv.DictWriter(stream, reader.fieldnames, lineterminator='\n')
			writer.writeheader()
			for row in reader:
				tid = row['trip_id']
				route = trips[tid]['route_id']
				if route.startswith('RAIL:'):
					original = dict(row)
					prev = last.get(tid)
					if prev:
						po, pn = prev
						key = edge(route, po['stop_id'], row['stop_id'])
						old = sec(row['arrival_time']) - sec(po['departure_time'])
						replacement = segments.get(key)
						if route.startswith('RAIL:MTR:'):
							replacement = hr_timing[tid]['segments'][
								int(row['stop_sequence']) - 2
							]
						all_edges.add(key)
						duration = replacement['seconds'] if replacement else old
						if duration < 0:
							raise ValueError('Negative input edge ' + key)
						dwell = sec(row['departure_time']) - sec(row['arrival_time'])
						arrival = sec(pn['departure_time']) + duration
						row.update(
							arrival_time=clock(arrival),
							departure_time=clock(arrival + dwell),
						)
						if replacement:
							used.add(key)
							proof['segments'][key] = replacement
							if duration != old:
								changed += 1
								trips_changed.add(tid)
					else:
						origins[tid] = original['departure_time']
					last[tid] = (original, dict(row))
					tails[tid] = row['arrival_time']
				writer.writerow(row)
			stream.flush()
			stream.detach()
		proof.update(
			mtr_trip_templates=len(hr_timing),
			mtr_distance_fallbacks=0,
			unique_segments_applied=len(used),
			stop_time_edges_changed=changed,
			trips_changed=len(trips_changed),
			line_coverage={
				line: {
					'applied': sum(
						k in used for k in all_edges if k.rsplit(':', 1)[0] == line
					),
					'total': sum(k.rsplit(':', 1)[0] == line for k in all_edges),
				}
				for line in sorted({k.rsplit(':', 1)[0] for k in all_edges})
			},
		)
		target.writestr(
			'mtr_api_provenance.json', json.dumps(proof, ensure_ascii=False)
		)
	dst.with_suffix('.tmp').replace(dst)
	# Independent output checks: schedules and nonrail calls are unchanged;
	# origin departures and every applied per-edge duration must match exactly.
	with zipfile.ZipFile(src) as old, zipfile.ZipFile(dst) as new:
		for name in old.namelist():
			if name != 'stop_times.txt':
				assert old.read(name) == new.read(name), name
		previous = {}
		n = 0
		ra = csv.DictReader(
			io.TextIOWrapper(old.open('stop_times.txt'), encoding='utf-8-sig')
		)
		rb = csv.DictReader(
			io.TextIOWrapper(new.open('stop_times.txt'), encoding='utf-8-sig')
		)
		import itertools

		for x, y in itertools.zip_longest(ra, rb):
			assert x is not None and y is not None
			tid = x['trip_id']
			route = trips[tid]['route_id']
			if not route.startswith('RAIL:'):
				assert x == y
			else:
				assert {
					k: v
					for k, v in x.items()
					if k not in ('arrival_time', 'departure_time')
				} == {
					k: v
					for k, v in y.items()
					if k not in ('arrival_time', 'departure_time')
				}
				if tid not in previous:
					assert (
						x['departure_time'] == y['departure_time']
						and x['arrival_time'] == y['arrival_time']
					)
				else:
					ox, ny = previous[tid]
					key = edge(route, ox['stop_id'], x['stop_id'])
					expected = (
						hr_timing[tid]['segments'][int(x['stop_sequence']) - 2][
							'seconds'
						]
						if route.startswith('RAIL:MTR:')
						else segments.get(key, {}).get(
							'seconds',
							sec(x['arrival_time']) - sec(ox['departure_time']),
						)
					)
					assert (
						sec(y['arrival_time']) - sec(ny['departure_time']) == expected
					)
				previous[tid] = (x, y)
			n += 1
	summary = {k: v for k, v in proof.items() if k not in ('segments', 'trips')}
	summary.update(output=str(dst), checked_stop_times=n, active=False)
	(out / 'merge_report.json').write_text(json.dumps(summary, indent=2))
	print(json.dumps(summary))
	from compile_mtr_od import compile_pairs

	compile_pairs(a.root, dst)
	from compile_mtr_interchanges import compile_interchanges

	compile_interchanges(a.root, dst)


if __name__ == '__main__':
	main()
