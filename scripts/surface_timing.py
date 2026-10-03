#!/usr/bin/env python3
"""Reproducible distance-weighted surface-transit timings and matched CSDI shapes.

Only blank stop times are filled. Published anchors and non-surface transit are
preserved. These are timetable estimates, never claimed as measured/live times.
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
from pathlib import Path
import tempfile
import zipfile

DATASETS = {'bus': 'td_rcd_1638844988873_41214', 'gmb': 'td_rcd_1697082463580_57453'}
MODEL = dict(version=2, method='distance_weighted_between_published_anchors',
	source_geometry='preserve_exactly_connected_source_part_order_else_connected_line_merge',
	max_stop_offset_m=100, shape_simplification_m=1,
	fallback='straight_line_distance_only_when_no_validated_route_path',
	traffic='No live traffic adjustment', dwell='Included implicitly in published total duration')


def sha(path):
	return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def fetch(root, offline=False, refresh=False):
	import requests
	base = root / 'data/surface/raw'
	base.mkdir(parents=True, exist_ok=True)
	manifest_path = base / 'manifest.json'
	manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
	for name, dataset in DATASETS.items():
		path = base / (name + '.zip')
		if path.exists() and not refresh:
			if manifest.get(name, {}).get('sha256') != sha(path):
				raise ValueError('Unverified surface source: ' + name)
			continue
		if offline:
			raise ValueError('Missing surface source; run surface_timing.py fetch online first: ' + name)
		url = 'https://portal.csdi.gov.hk/geoportal/rest/metadata/item/' + dataset
		r = requests.get(url, timeout=60)
		r.raise_for_status()
		metadata = r.json()
		fileid = metadata['_source']['fileid'].replace('-', '')
		download = 'https://static.csdi.gov.hk/csdi-webpage/download/' + fileid + '/fgdb'
		r = requests.get(download, timeout=180)
		r.raise_for_status()
		temp = path.with_suffix('.part')
		temp.write_bytes(r.content)
		with zipfile.ZipFile(temp) as z:
			if z.testzip():
				raise ValueError('Invalid source archive: ' + name)
		temp.replace(path)
		save(base / (name + '-metadata.json'), metadata)
		manifest[name] = dict(dataset=dataset, metadata_url=url, url=download,
			sha256=sha(path), retrieved_at=datetime.now(timezone.utc).isoformat(),
			source_modified=metadata['_source'].get('sys_modified_dt'),
			credit='Hong Kong Transport Department / CSDI')
		save(manifest_path, manifest)
	return manifest


def connected_source_line(geometry):
	"""Preserve source traversal order when multipart endpoints exactly agree.

	Generic linemerge treats repeated road junctions as branches. A published
	circular route can therefore become a MultiLineString even though its parts
	already form one continuous ordered journey. Keep every traversal without
	inventing any segment; unordered parts still use the connected-line fallback.
	"""
	from shapely.geometry import LineString
	from shapely.ops import linemerge
	if geometry.geom_type == 'LineString':
		return geometry if not geometry.is_empty else None
	if geometry.geom_type != 'MultiLineString' or geometry.is_empty:
		return None
	parts = [list(part.coords) for part in geometry.geoms]
	if all(len(part) >= 2 for part in parts) and all(a[-1] == b[0] for a, b in zip(parts, parts[1:])):
		coordinates = list(parts[0])
		for part in parts[1:]:
			coordinates.extend(part[1:])
		return LineString(coordinates)
	merged = linemerge(geometry)
	return merged if merged.geom_type == 'LineString' and not merged.is_empty else None


def source_lines(root):
	"""Read pinned originals directly with GDAL; no geopandas dependency."""
	from pyogrio.raw import read
	from shapely import from_wkb
	from shapely.ops import transform
	from pyproj import Transformer
	result = defaultdict(list)
	for name in DATASETS:
		path = root / 'data/surface/raw' / (name + '.zip')
		with zipfile.ZipFile(path) as z:
			folders = {n.split('/')[0] for n in z.namelist() if '.gdb/' in n}
		if len(folders) != 1:
			raise ValueError('Expected one geodatabase')
		gdb = '/vsizip/' + str(path.resolve()) + '/' + folders.pop()
		meta, _, geometries, values = read(gdb)
		convert = Transformer.from_crs(meta['crs'], 'EPSG:2326', always_xy=True)
		for i, raw in enumerate(geometries):
			props = {field: values[j][i] for j, field in enumerate(meta['fields'])}
			line = connected_source_line(from_wkb(raw))
			if line is None:
				continue  # Do not invent bridges between disconnected source components.
			line = transform(convert.transform, line).simplify(MODEL['shape_simplification_m'])
			result[str(int(props['ROUTE_ID']))].append(dict(line=line, source=name,
				sequence=int(props['ROUTE_SEQ']), start=str(int(props['ST_STOP_ID'])),
				end=str(int(props['ED_STOP_ID']))))
	return result


def match_line(line, points, tolerance=100):
	"""Match stops monotonically along the path, including loops/repeated roads.

	Project to every segment, collapse adjacent candidate runs, then use dynamic
	programming. First/last stops must be near the path endpoints. Never choose an
	unrelated nearby route using a nearest-line-only match.
	"""
	import numpy as np
	from shapely.geometry import LineString
	xy = np.asarray(line.coords)[:, :2]
	p = np.asarray(points)
	if np.linalg.norm(p[0] - xy[-1]) + np.linalg.norm(p[-1] - xy[0]) < np.linalg.norm(p[0] - xy[0]) + np.linalg.norm(p[-1] - xy[-1]):
		xy = xy[::-1].copy()
		line = LineString(xy)
	if max(np.linalg.norm(p[0] - xy[0]), np.linalg.norm(p[-1] - xy[-1])) > tolerance:
		return None
	a, v = xy[:-1], np.diff(xy, axis=0)
	length = np.linalg.norm(v, axis=1)
	cumulative = np.r_[0, np.cumsum(length)]
	candidates = []
	for index, point in enumerate(p):
		t = np.clip(np.sum((point - a) * v, axis=1) / np.maximum(length**2, 1e-12), 0, 1)
		error = np.linalg.norm(a + t[:, None] * v - point, axis=1)
		positions = cumulative[:-1] + t * length
		valid = np.flatnonzero(error <= tolerance)
		groups = np.split(valid, np.where(np.diff(valid) > 1)[0] + 1)
		choices = []
		for group in groups:
			if len(group):
				j = int(group[np.argmin(error[group])])
				choices.append((float(positions[j]), float(error[j])))
		if index == 0:
			choices = [(0., float(np.linalg.norm(point - xy[0])))]
		elif index == len(p) - 1:
			choices = [(float(cumulative[-1]), float(np.linalg.norm(point - xy[-1])))]
		if not choices:
			return None
		candidates.append(choices)
	states = [(e * e, [s], [e]) for s, e in candidates[0]]
	for choices in candidates[1:]:
		next_states = []
		for s, e in choices:
			previous = [state for state in states if s >= state[1][-1] - 1e-6]
			if previous:
				cost, path, errors = min(previous, key=lambda state: state[0])
				next_states.append((cost + e * e, path + [s], errors + [e]))
		states = next_states
		if not states:
			return None
	_, distances, errors = min(states, key=lambda state: state[0])
	# Distinct stops cannot collapse onto the same position on an unrelated road.
	for i in range(1, len(distances)):
		if distances[i] - distances[i-1] < 1 and np.linalg.norm(p[i] - p[i-1]) > 15:
			return None
	return dict(line=line, distances=distances, vertex_distances=cumulative.tolist(), max_offset_m=max(errors))


def seconds(value):
	h, m, s = map(int, value.split(':'))
	return h * 3600 + m * 60 + s


def clock(value):
	return f'{value // 3600:02d}:{value // 60 % 60:02d}:{value % 60:02d}'


def interpolate(rows, distances):
	"""Keep every supplied time byte-for-byte; fill only blank fields."""
	out = [dict(r) for r in rows]
	filled = 0
	for r in out:
		if bool(r['arrival_time']) != bool(r['departure_time']):
			r['arrival_time'] = r['arrival_time'] or r['departure_time']
			r['departure_time'] = r['departure_time'] or r['arrival_time']
			r['timepoint'] = '0'
	anchors = [i for i, r in enumerate(out) if r['arrival_time'] and r['departure_time']]
	if not anchors or anchors[0] != 0 or anchors[-1] != len(out) - 1:
		raise ValueError('Missing endpoint timing anchor')
	for start, end in zip(anchors, anchors[1:]):
		if end - start == 1:
			continue
		begin, finish = seconds(out[start]['departure_time']), seconds(out[end]['arrival_time'])
		span = distances[end] - distances[start]
		if span <= 0 or finish - begin < end - start:
			raise ValueError('Invalid distance or duration between anchors')
		last = begin
		for i in range(start + 1, end):
			t = round(begin + (finish - begin) * (distances[i] - distances[start]) / span)
			t = max(last + 1, min(t, finish - (end - i)))
			out[i].update(arrival_time=clock(t), departure_time=clock(t), timepoint='0')
			last = t
			filled += 1
	return out, filled


def fallback_distances(points):
	# EPSG:2326 local grid distances; this is explicitly NOT a road route.
	d = [0.]
	for a, b in zip(points, points[1:]):
		d.append(d[-1] + math.dist(a, b))
	return d


def merge(root, source, destination):
	from pyproj import Transformer
	from shapely.ops import transform
	manifest = fetch(root, offline=True)
	lines = source_lines(root)
	to_grid = Transformer.from_crs('EPSG:4326', 'EPSG:2326', always_xy=True)
	to_geo = Transformer.from_crs('EPSG:2326', 'EPSG:4326', always_xy=True)
	stats = Counter()
	patterns, shapes, trip_shapes, trip_patterns = {}, {}, {}, {}
	seen = set()
	destination.parent.mkdir(parents=True, exist_ok=True)
	temp = destination.with_suffix('.part')
	with zipfile.ZipFile(source) as zin, zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as zout:
		if 'surface_timing_provenance.json' in zin.namelist():
			raise ValueError('Use the pre-surface source feed, not an already patched feed')
		def read(name):
			return csv.DictReader(io.StringIO(zin.read(name).decode('utf-8-sig')))
		routes = {r['route_id']: r for r in read('routes.txt')}
		trips = {r['trip_id']: r for r in read('trips.txt')}
		stops = {r['stop_id']: r for r in read('stops.txt')}
		def pattern_for(trip, rows):
			rid = trip['route_id']
			ids = [r['stop_id'] for r in rows]
			key = hashlib.sha256(json.dumps([rid, ids]).encode()).hexdigest()[:20]
			if key in patterns:
				return key, patterns[key]
			points = [to_grid.transform(float(stops[s]['stop_lon']), float(stops[s]['stop_lat'])) for s in ids]
			matches = []
			for candidate in lines.get(rid, []):
				if (candidate['start'], candidate['end']) != (ids[0], ids[-1]):
					continue
				fit = match_line(candidate['line'], points, MODEL['max_stop_offset_m'])
				if fit:
					matches.append((candidate, fit))
			item = dict(route_id=rid, route_name=routes[rid]['route_short_name'], stop_ids=ids,
				kind='straight_line_distance_fallback', distances_m=fallback_distances(points),
				warning='No validated route path; distances are straight-line estimates, not road distances.',
				fallback_reason=('no_source_route' if rid not in lines else 'endpoint_or_stop_sequence_mismatch'))
			if matches:
				candidate, fit = min(matches, key=lambda pair: pair[1]['max_offset_m'])
				item.update(kind='csdi_route_distance', distances_m=fit['distances'],
					source=candidate['source'], source_sequence=candidate['sequence'],
					max_stop_offset_m=round(fit['max_offset_m'], 2), warning=None, fallback_reason=None,
					shape_id='SURFACE:' + key)
				# Use the identical metric distances for shapes and stop times. A
				# grid -> WGS84 -> grid round-trip can move the endpoint by 1 mm,
				# which would make GTFS stop distance exceed shape distance.
				shapes[item['shape_id']] = (transform(to_geo.transform, fit['line']), fit['vertex_distances'])
			patterns[key] = item
			return key, item
		with zin.open('stop_times.txt') as incoming, zout.open('stop_times.txt', 'w') as outgoing:
			reader = csv.DictReader(io.TextIOWrapper(incoming, encoding='utf-8-sig'))
			fields = list(reader.fieldnames)
			for field in ('timepoint', 'shape_dist_traveled'):
				if field not in fields:
					fields.append(field)
			with io.TextIOWrapper(outgoing, encoding='utf-8', newline='') as text:
				writer = csv.DictWriter(text, fields, lineterminator='\n')
				writer.writeheader()
				for tid, group in groupby(reader, key=lambda row: row['trip_id']):
					if tid in seen:
						raise ValueError('Non-contiguous stop_times trip: ' + tid)
					seen.add(tid)
					rows = list(group)
					trip = trips[tid]
					if routes[trip['route_id']]['route_type'] in ('3', '0', '4') and not trip['route_id'].startswith('RAIL:'):
						rows.sort(key=lambda row: int(row['stop_sequence']))
						key, item = pattern_for(trip, rows)
						try:
							rows, n = interpolate(rows, item['distances_m'])
						except ValueError as error:
							raise ValueError(tid + ': ' + str(error)) from error
						stats['filled_stop_rows'] += n
						stats[item['kind'] + '_trips'] += 1
						trip_patterns[tid] = key
						if item.get('shape_id'):
							trip_shapes[tid] = item['shape_id']
							for r, distance in zip(rows, item['distances_m']):
								r['shape_dist_traveled'] = f'{distance:.3f}'
					writer.writerows(rows)
		for name in zin.namelist():
			if name in ('stop_times.txt', 'trips.txt', 'shapes.txt'):
				continue
			with zin.open(name) as incoming, zout.open(name, 'w') as outgoing:
				import shutil
				shutil.copyfileobj(incoming, outgoing)
		reader = read('trips.txt')
		fields = list(reader.fieldnames)
		if 'shape_id' not in fields:
			fields.append('shape_id')
		with zout.open('trips.txt', 'w') as out, io.TextIOWrapper(out, encoding='utf-8', newline='') as text:
			writer = csv.DictWriter(text, fields, lineterminator='\n')
			writer.writeheader()
			for trip in reader:
				if trip['trip_id'] in trip_shapes:
					trip['shape_id'] = trip_shapes[trip['trip_id']]
				writer.writerow(trip)
		fields = ['shape_id', 'shape_pt_lat', 'shape_pt_lon', 'shape_pt_sequence', 'shape_dist_traveled']
		if 'shapes.txt' in zin.namelist():
			fields = list(dict.fromkeys([*read('shapes.txt').fieldnames, *fields]))
		with zout.open('shapes.txt', 'w') as out, io.TextIOWrapper(out, encoding='utf-8', newline='') as text:
			writer = csv.DictWriter(text, fields, lineterminator='\n')
			writer.writeheader()
			if 'shapes.txt' in zin.namelist():
				writer.writerows(read('shapes.txt'))
			for sid, (line, vertex_distances) in shapes.items():
				for i, ((lon, lat), distance) in enumerate(zip(line.coords, vertex_distances), 1):
					writer.writerow(dict(shape_id=sid, shape_pt_lat=f'{lat:.8f}', shape_pt_lon=f'{lon:.8f}',
						shape_pt_sequence=i, shape_dist_traveled=f'{distance:.3f}'))
		stats['matched_patterns'] = sum(p['kind'] == 'csdi_route_distance' for p in patterns.values())
		stats['fallback_patterns'] = len(patterns) - stats['matched_patterns']
		proof = dict(model=MODEL, source_manifest=manifest, input_sha256=sha(source),
			statistics=dict(stats), patterns=patterns, trips=trip_patterns)
		zout.writestr('surface_timing_provenance.json', json.dumps(proof, ensure_ascii=False, separators=(',', ':')))
	temp.replace(destination)
	save(root / 'data/surface/report.json', dict(model=MODEL, source_manifest=manifest,
		input_sha256=proof['input_sha256'], output_sha256=sha(destination), statistics=dict(stats), patterns=patterns))
	print(json.dumps(dict(stats), indent=2), flush=True)
	return proof


if __name__ == '__main__':
	p = argparse.ArgumentParser(description=__doc__)
	p.add_argument('command', choices=['fetch', 'merge'])
	p.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
	p.add_argument('--offline', action='store_true')
	p.add_argument('--refresh', action='store_true')
	p.add_argument('--input', type=Path)
	p.add_argument('--output', type=Path)
	a = p.parse_args()
	root = a.root.resolve()
	if a.command == 'fetch':
		fetch(root, a.offline, a.refresh)
	else:
		merge(root, a.input or root / 'data/generated/hk-transit-INDOOR.gtfs.zip',
			a.output or root / 'data/generated/hk-transit-SURFACE.gtfs.zip')
