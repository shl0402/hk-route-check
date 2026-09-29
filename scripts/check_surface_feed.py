#!/usr/bin/env python3
"""Check timing anchors, preserved rail, geometry references and reproducibility."""
import argparse
import csv
import io
import json
from pathlib import Path
import zipfile


def check(base, candidate):
	with zipfile.ZipFile(base) as old, zipfile.ZipFile(candidate) as new:
		def rows(z, name):
			return csv.DictReader(io.TextIOWrapper(z.open(name), encoding='utf-8-sig'))
		for name in old.namelist():
			if name not in ('trips.txt', 'stop_times.txt', 'shapes.txt'):
				assert old.read(name) == new.read(name), 'Changed unrelated table: ' + name
		old_trips = {r['trip_id']: r for r in rows(old, 'trips.txt')}
		new_trips = {r['trip_id']: r for r in rows(new, 'trips.txt')}
		assert old_trips.keys() == new_trips.keys(), 'Changed trip set'
		for tid, trip in old_trips.items():
			for k, v in trip.items():
				if k != 'shape_id':
					assert new_trips[tid][k] == v, (tid, k)
		proof = json.loads(new.read('surface_timing_provenance.json'))
		count = anchors = filled = rail = 0
		before = iter(rows(old, 'stop_times.txt'))
		after = iter(rows(new, 'stop_times.txt'))
		while True:
			a, b = next(before, None), next(after, None)
			assert (a is None) == (b is None), 'Changed stop row count'
			if a is None:
				break
			count += 1
			assert (a['trip_id'], a['stop_sequence'], a['stop_id']) == (b['trip_id'], b['stop_sequence'], b['stop_id']), 'Changed stop sequence'
			is_rail = old_trips[a['trip_id']]['route_id'].startswith('RAIL:')
			for k, v in a.items():
				if k in ('arrival_time', 'departure_time') and not v:
					continue
				if k in ('shape_dist_traveled', 'timepoint') and not is_rail:
					continue
				assert b[k] == v, ('Changed source field', a['trip_id'], k, v, b[k])
			if a['arrival_time']:
				anchors += 1
			elif b['arrival_time']:
				filled += 1
				assert b['timepoint'] == '0', 'Estimate labelled exact'
			if is_rail:
				rail += 1
		shape_ids = set()
		last = {}
		for row in rows(new, 'shapes.txt'):
			sid = row['shape_id']
			shape_ids.add(sid)
			if sid.startswith('SURFACE:'):
				d = float(row['shape_dist_traveled'])
				assert d >= last.get(sid, 0), 'Non-monotonic shape distance'
				last[sid] = d
		for key, pattern in proof['patterns'].items():
			if pattern['kind'] == 'csdi_route_distance':
				assert pattern['shape_id'] in shape_ids
				assert pattern['max_stop_offset_m'] <= proof['model']['max_stop_offset_m']
		print(json.dumps(dict(stop_rows=count, preserved_timed_rows=anchors, filled_rows=filled,
			unchanged_rail_rows=rail, road_shapes=len([s for s in shape_ids if s.startswith('SURFACE:')])), indent=2))


if __name__ == '__main__':
	p = argparse.ArgumentParser(description=__doc__)
	p.add_argument('base', type=Path)
	p.add_argument('candidate', type=Path)
	a = p.parse_args()
	check(a.base, a.candidate)
