#!/usr/bin/env python3
"""Reproducible LandsD indoor import and conservative GTFS station pathways.

Raw geometry is retained. Verified physical platforms and entrances are matched
individually, using official platform evidence and centimetre-bounded topology
normalization. Unsupported calls retain legacy routing and are reported.
Durations are model estimates, NOT times supplied/measured by LandsD.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import heapq
import io
import json
import math
from pathlib import Path
import re
import time
import zipfile

import requests

import landsd_enrich as landsd


NETWORK_URL = 'https://static.csdi.gov.hk/csdi-webpage/download/common/a381721a8e6956e7448bb63f5cc7f37d4cb1be0a42b8e4d94e414c6211ed27ce'
CATALOG_URL = 'https://portal.csdi.gov.hk/geoportal/?datasetId=landsd_rcd_1742809197336_78665&lang=en'
RAW = 'data/landsd/raw/indoor-network.zip'
META = 'data/landsd/raw/indoor-network-manifest.json'
FGDB_URL = 'https://static.csdi.gov.hk/csdi-webpage/download/87ac16f68b07558caaa5f73a22764778/fgdb'
FGDB_RAW = 'data/landsd/raw/indoor-network-fgdb.zip'
FGDB_META = 'data/landsd/raw/indoor-network-fgdb-manifest.json'
OUTPUT = 'data/landsd/indoor'
MODEL = {
	'version': 2,
	'topology_horizontal_tolerance_m': 0.01,
	'topology_vertical_tolerance_m': 0.005,
	'landmark_horizontal_tolerance_m': 0.25,
	'walk_m_s': 1.2,
	'stairs_m_s': 0.6,
	'escalator_m_s': 0.5,
	'lift_vertical_m_s': 1.0,
	'lift_wait_seconds': 20,
	'notes': 'Estimated movement time, excluding train waiting/boarding slack. Lift waiting is a fixed modelling assumption. Not a wheelchair route guarantee.',
}
LINE_NAMES = {
	'ISL': 'Island Line',
	'TWL': 'Tsuen Wan Line',
	'KTL': 'Kwun Tong Line',
	'TKL': 'Tseung Kwan O Line',
	'TML': 'Tuen Ma Line',
	'EAL': 'East Rail Line',
	'TCL': 'Tung Chung Line',
	'AEL': 'Airport Express',
	'DRL': 'Disneyland Resort Line',
	'SIL': 'South Island Line',
}
MODES = {1: 1, 2: 1, 4: 1, 5: 1, 8: 4, 9: 3, 10: 5, 11: 1, 12: 2,
	15: 1, 16: 4, 17: 3, 18: 5, 19: 1, 20: 2}


def read_archive(path):
	"""Read without extracting paths; reject corrupt, incomplete or changed schemas."""
	with zipfile.ZipFile(path) as archive:
		if archive.testzip():
			raise ValueError('Corrupt indoor network archive')
		result = {}
		for name in archive.namelist():
			if not name.endswith('.geojson'):
				continue
			data = json.loads(archive.read(name).decode('utf-8-sig'))
			if data.get('type') != 'FeatureCollection':
				raise ValueError('Invalid network GeoJSON: ' + name)
			key = Path(name).stem
			if key in result:
				raise ValueError('Duplicate network table: ' + key)
			result[key] = data
	for key in ('PedestrianRoute', 'PedRouteRelFloorPoly', 'AccessTime', 'AccessTimeDetails', 'dDirection', 'dPedFeatureType'):
		if key not in result:
			raise ValueError('Missing source table: ' + key)
	ids = set()
	for feature in result['PedestrianRoute']['features']:
		props = feature['properties']
		pid = props['PedestrianRouteID']
		if pid in ids:
			raise ValueError('Duplicate route ID: ' + str(pid))
		ids.add(pid)
		geometry = feature.get('geometry') or {}
		if geometry.get('type') not in ('LineString', 'Point'):
			raise ValueError('Invalid route geometry: ' + str(pid))
		coords = [geometry['coordinates']] if geometry['type'] == 'Point' else geometry['coordinates']
		for c in coords:
			if len(c) != 3 or not all(math.isfinite(x) for x in c) or not (113.8 < c[0] < 114.5 and 22.08 < c[1] < 22.6):
				raise ValueError('Invalid WGS84/Z coordinate: ' + str(pid))
	if not ids:
		raise ValueError('Empty indoor network')
	return result


def fetch(root, offline=False, refresh=False, source=NETWORK_URL):
	path, meta = root / RAW, root / META
	if not refresh and path.exists() and meta.exists():
		manifest = json.loads(meta.read_text())
		if landsd.sha(path) != manifest['sha256']:
			raise ValueError('Indoor source checksum mismatch; restore cache or explicitly refresh')
		read_archive(path)
		return path
	if offline:
		raise ValueError('Missing verified indoor network cache; run indoor_network.py fetch online first')
	path.parent.mkdir(parents=True, exist_ok=True)
	for attempt in range(4):
		try:
			response = requests.get(source, timeout=120)
			response.raise_for_status()
			temp = path.with_suffix('.zip.part')
			temp.write_bytes(response.content)
			tables = read_archive(temp)
			temp.replace(path)
			landsd.save(meta, dict(url=source, catalog=CATALOG_URL,
				retrieved_at=datetime.now(timezone.utc).isoformat(), sha256=landsd.sha(path),
				bytes=path.stat().st_size, tables={k: len(v['features']) for k, v in tables.items()}))
			print('Verified indoor network:', len(tables['PedestrianRoute']['features']), 'segments', flush=True)
			return path
		except (requests.RequestException, ValueError, zipfile.BadZipFile):
			if attempt == 3:
				raise
			time.sleep(2 ** attempt)


def fetch_original(root, offline=False, refresh=False):
	path, meta = root / FGDB_RAW, root / FGDB_META
	if not refresh and path.exists() and meta.exists():
		if landsd.sha(path) != json.loads(meta.read_text())['sha256']:
			raise ValueError('Original indoor network checksum mismatch')
		return path
	if offline:
		raise ValueError('Missing original indoor network source; run fetch online first')
	response = requests.get(FGDB_URL, timeout=180)
	response.raise_for_status()
	path.parent.mkdir(parents=True, exist_ok=True)
	temp = path.with_suffix('.zip.part')
	temp.write_bytes(response.content)
	with zipfile.ZipFile(temp) as archive:
		if archive.testzip() or not any(n.endswith('.gdbtable') for n in archive.namelist()):
			raise ValueError('Invalid original FGDB download')
	temp.replace(path)
	landsd.save(meta, dict(url=FGDB_URL, retrieved_at=datetime.now(timezone.utc).isoformat(),
		sha256=landsd.sha(path), bytes=path.stat().st_size))
	return path


def original_tables(root, companion):
	"""Use original 3D geometry; the convenience GeoJSON collapses vertical lifts."""
	import pyogrio
	from pyogrio.raw import read
	from pyproj import Transformer
	from shapely import from_wkb
	from shapely.geometry import mapping
	from shapely.ops import transform

	path = fetch_original(root, offline=True)
	with zipfile.ZipFile(path) as archive:
		folders = {n.split('/')[0] for n in archive.namelist() if '.gdb/' in n}
	if len(folders) != 1:
		raise ValueError('Expected one source geodatabase')
	gdb = '/vsizip/' + str(path.resolve()) + '/' + folders.pop()
	convert = Transformer.from_crs('EPSG:2326', 'EPSG:4326', always_xy=True)
	result = {k: v for k, v in companion.items() if k.startswith('d')}
	def scalar(v):
		if hasattr(v, 'item'):
			v = v.item()
		if isinstance(v, float) and not math.isfinite(v):
			return None
		if isinstance(v, datetime):
			return v.isoformat()
		return v
	for layer, _ in pyogrio.list_layers(gdb):
		print('Reading original indoor layer:', layer, flush=True)
		meta, _, geometries, values = read(gdb, layer=layer)
		fs = []
		for i in range(len(values[0])):
			props = {name: scalar(values[j][i]) for j, name in enumerate(meta['fields'])}
			geometry = None
			if geometries is not None:
				g = from_wkb(geometries[i])
				if g.geom_type == 'MultiLineString' and len(g.geoms) == 1:
					g = g.geoms[0]
				# Only horizontal datum is transformed. Source HKPD height is retained.
				geometry = mapping(transform(convert.transform, g))
			fs.append(dict(type='Feature', properties=props, geometry=geometry))
		result[layer] = dict(type='FeatureCollection', features=fs)
	old = {f['properties']['PedestrianRouteID']: f for f in companion['PedestrianRoute']['features']}
	new = {f['properties']['PedestrianRouteID']: f for f in result['PedestrianRoute']['features']}
	audit = dict(original_records=len(new), geojson_records=len(old),
		original_only=sorted(set(new) - set(old)), geojson_only=sorted(set(old) - set(new)),
		point_only_geojson=[], geometry_disagreements=[], attribute_disagreements=[])
	for pid in sorted(set(old) & set(new)):
		a, b = old[pid], new[pid]
		if a['geometry']['type'] == 'Point':
			audit['point_only_geojson'].append(pid)
			continue
		if b['geometry']['type'] != 'LineString':
			audit['geometry_disagreements'].append(pid)
			continue
		ac, bc = a['geometry']['coordinates'], b['geometry']['coordinates']
		if len(ac) != len(bc) or any(horizontal(x, y) > 0.5 or abs(x[2] - y[2]) > 0.01 for x, y in zip(ac, bc)):
			audit['geometry_disagreements'].append(pid)
		if any(a['properties'].get(k) != b['properties'].get(k) for k in ('Direction', 'FeatureType', 'Enabled', 'AccessTimeID')):
			audit['attribute_disagreements'].append(pid)
	audit['table_counts'] = {k: dict(original=len(v['features']), geojson=len(companion.get(k, {}).get('features', []))) for k, v in result.items() if not k.startswith('d')}
	return result, audit


def vertex(c):
	# Published longitude/latitude precision is 1e-8 degrees; Z is millimetres.
	# This only removes floating point representation noise, not spatial gaps.
	return (round(c[0], 8), round(c[1], 8), round(c[2], 3))


def horizontal(a, b):
	lat = math.radians((a[1] + b[1]) / 2)
	return math.hypot((a[0] - b[0]) * 111195 * math.cos(lat), (a[1] - b[1]) * 111195)


def edge_cost(a, b, mode):
	distance = math.hypot(horizontal(a, b), b[2] - a[2])
	if mode == 5:
		seconds = abs(b[2] - a[2]) / MODEL['lift_vertical_m_s']
	else:
		speed = MODEL['stairs_m_s'] if mode == 2 else MODEL['escalator_m_s'] if mode == 4 else MODEL['walk_m_s']
		seconds = distance / speed
	return distance, seconds


def make_graph(features):
	from indoor_matching import coincident_vertices
	canonical = coincident_vertices([vertex(c) for f in features if f['geometry']['type']=='LineString' for c in f['geometry']['coordinates']], horizontal)
	edges, adjacency, rejected = [], defaultdict(list), []
	for feature in sorted(features, key=lambda f: f['properties']['PedestrianRouteID']):
		p = feature['properties']
		pid = p['PedestrianRouteID']
		reason = None
		if feature['geometry']['type'] != 'LineString':
			reason = 'source contains point-only path: missing second endpoint'
		elif p.get('Enabled') != 1:
			reason = 'disabled or unknown access'
		elif p.get('Direction') not in (-1, 0, 1):
			reason = 'unknown direction'
		elif p.get('FeatureType') not in MODES:
			reason = 'unsupported path type'
		elif p.get('AccessTimeID') is not None:
			# GTFS pathways cannot encode passage opening hours. Preserve in the
			# map data but do not expose a sometimes-closed path as always open.
			reason = 'time-restricted passage cannot be encoded in static GTFS pathways'
		if reason:
			rejected.append(dict(route_id=pid, reason=reason))
			continue
		coords = [canonical[vertex(c)] for c in feature['geometry']['coordinates']]
		mode = MODES[p['FeatureType']]
		# A lift's intermediate geometry vertices are not additional floors.
		# Keep one lift edge between its source endpoints, with one waiting cost.
		if mode == 5:
			coords = [coords[0], coords[-1]]
		for i, (a, b) in enumerate(zip(coords, coords[1:])):
			if a == b:
				continue
			length, seconds = edge_cost(a, b, mode)
			if mode == 5:
				seconds += MODEL['lift_wait_seconds'] / (len(coords) - 1)
			if p['Direction'] == -1:
				a, b = b, a
			edge = dict(id=f'{pid}:{i}', source_id=pid, a=a, b=b,
				mode=mode, length=length, seconds=max(1, math.ceil(seconds)),
				both=p['Direction'] == 0, name=p.get('AliasNameEN') or 'Station passage')
			edges.append(edge)
			adjacency[a].append((b, edge['seconds'], edge))
			if edge['both']:
				adjacency[b].append((a, edge['seconds'], edge))
	from indoor_matching import collapse_lift_splits
	edges = collapse_lift_splits(edges)
	adjacency = defaultdict(list)
	for edge in edges:
		adjacency[edge['a']].append((edge['b'], edge['seconds'], edge))
		if edge['both']:
			adjacency[edge['b']].append((edge['a'], edge['seconds'], edge))
	return edges, adjacency, rejected


def shortest(adjacency, start, end):
	queue, best, previous = [(0, start)], {start: 0}, {}
	while queue:
		cost, node = heapq.heappop(queue)
		if cost != best[node]:
			continue
		if node == end:
			path = []
			while node != start:
				before, edge = previous[node]
				path.append(edge)
				node = before
			return cost, list(reversed(path))
		for dest, weight, edge in adjacency.get(node, []):
			candidate = cost + weight
			if candidate < best.get(dest, math.inf):
				best[dest] = candidate
				previous[dest] = (node, edge)
				heapq.heappush(queue, (candidate, dest))
	return None


def exit_code(name):
	match = re.fullmatch(r'Exit\s+([A-Z]\d*)', (name or '').strip(), re.I)
	return match.group(1).upper() if match else None


def named_lines(name):
	"""Avoid classifying 'South Island Line' as 'Island Line'."""
	text = (name or '').casefold()
	found = {code for code, label in LINE_NAMES.items() if label.casefold() in text}
	if 'SIL' in found:
		found.discard('ISL')
	return found


def csv_rows(archive, name):
	return list(csv.DictReader(io.StringIO(archive.read(name).decode('utf-8-sig')))) if name in archive.namelist() else []


def compile_data(root, source, output):
	"""Validate every source, export all layers, activate only complete station matches."""
	companion = read_archive(fetch(root, offline=True))
	tables, format_audit = original_tables(root, companion)
	cache = landsd.fetch(root, offline=True)
	dest = root / OUTPUT
	dest.mkdir(parents=True, exist_ok=True)
	for key, table in tables.items():
		landsd.save(dest / (key + '.geojson'), table)
	venues = landsd.features(cache / 'venues.geojson')
	for layer in ('venues', *landsd.LAYERS):
		fs = venues if layer == 'venues' else [f for v in venues for f in landsd.features(cache / v['properties']['venue_id'] / (layer + '.geojson'))]
		landsd.save(dest / (layer + '.geojson'), dict(type='FeatureCollection', features=fs))
	floor_rel = {f['properties']['FloorID']: f['properties']['FloorPolyID'] for f in tables['PedRouteRelFloorPoly']['features']}
	companion_floors = {f['properties']['FloorID']: f['properties']['FloorPolyID'] for f in companion['PedRouteRelFloorPoly']['features']}
	format_audit['floor_identifier_differences'] = [dict(floor_id=k, original=v, geojson=companion_floors[k])
		for k, v in floor_rel.items() if k in companion_floors and v != companion_floors[k]]
	by_floor = defaultdict(list)
	for f in tables['PedestrianRoute']['features']:
		fid = f['properties'].get('FloorID')
		for alias in {floor_rel.get(fid), companion_floors.get(fid)} - {None}:
			by_floor[alias].append(f)
	report = dict(format=1, input_gtfs_sha256=landsd.sha(source), model=MODEL, sources={
		'network': json.loads((root / META).read_text()),
		'original_network': json.loads((root / FGDB_META).read_text()),
		'map_manifest_sha256': landsd.sha(cache / 'manifest.json'),
	}, cross_format_validation=format_audit, network_segments=len(tables['PedestrianRoute']['features']), stations=[],
		limitations=[
			'Mapped journeys use source-backed physical platform areas; shared platform areas do not identify a particular train door.',
			'Physical platform areas are matched to journey endpoints using cached operator platform/track observations; unresolved calls retain legacy routing.',
			'Time-restricted passages are retained in map data and excluded from static routing.',
			'Walk durations are configurable model estimates; crowds, lift outages and live accessibility are not represented.',
			'Indoor import does not repair missing outdoor OSM footpaths.',
		])
	from indoor_routing import compile_stations, write_stop_times
	with zipfile.ZipFile(source) as archive:
		stops, levels, paths, proof, contexts, resolved = compile_stations(root, archive, cache, dest, tables, by_floor, venues, report)
		report['activated_stations'] = sum(s['status'] == 'activated' for s in report['stations'])
		report['partial_stations'] = sum(s['status'] == 'partial' for s in report['stations'])
		report['withheld_stations'] = sum(s['status'] == 'withheld' for s in report['stations'])
		report['pathways'] = len(paths)
		if not resolved:
			raise ValueError('No verified physical boarding areas; refusing to publish')
		output.parent.mkdir(parents=True, exist_ok=True)
		temp = output.with_suffix('.tmp')
		with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as target:
			for name in archive.namelist():
				if name not in ('stops.txt', 'stop_times.txt', 'levels.txt', 'pathways.txt', 'indoor_provenance.json'):
					target.writestr(name, archive.read(name))
			for name, rows in (('stops.txt', stops), ('levels.txt', levels), ('pathways.txt', paths)):
				fields = list(dict.fromkeys(k for row in rows for k in row))
				target.writestr(name, landsd.csv_bytes(rows, fields))
			report['mapped_stop_calls'], report['legacy_stop_calls'] = write_stop_times(archive, target, proof, contexts, resolved)
			target.writestr('indoor_provenance.json', json.dumps(report, ensure_ascii=False))
		temp.replace(output)
		landsd.save(dest / 'validation.json', report)
		landsd.save(dest / 'stations.json', dict(attribution='Lands Department, HKSAR Government',
			stations=[dict(venue_id=s['venue_id'], name=s['name'], routing_status=s['status'],
				network_segments=s['network_segments'], reasons=s['reasons'],
				excluded_entrances=s.get('excluded_entrances',[]), excluded_platforms=s.get('excluded_platforms',[])) for s in report['stations']]))

	print(json.dumps({k: report[k] for k in ('network_segments', 'activated_stations', 'partial_stations', 'withheld_stations', 'pathways')}, indent=2), flush=True)
	return report


def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument('command', choices=('fetch', 'compile'))
	parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
	parser.add_argument('--offline', action='store_true')
	parser.add_argument('--refresh', action='store_true')
	parser.add_argument('--source-url', default=NETWORK_URL)
	parser.add_argument('--input', type=Path)
	parser.add_argument('--output', type=Path)
	args = parser.parse_args()
	if args.command == 'fetch':
		fetch(args.root, args.offline, args.refresh, args.source_url)
		fetch_original(args.root, args.offline, args.refresh)
	else:
		compile_data(args.root, args.input or args.root / 'data/generated/hk-transit-LANDSD.gtfs.zip',
			args.output or args.root / 'data/generated/hk-transit-INDOOR.gtfs.zip')


if __name__ == '__main__':
	main()
