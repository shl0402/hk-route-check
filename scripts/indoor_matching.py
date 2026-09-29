"""Evidence-based station matching shared by upstream and embedded rebuilds."""
import hashlib
import json
import math
import re
import unicodedata
from collections import defaultdict


def platform_codes(value):
	text = str(value or '').strip()
	if not re.fullmatch(r'\d+[A-Za-z]?(?:\s*(?:or|/|,|&)\s*\d+[A-Za-z]?)*', text):
		return ()
	return tuple(sorted(set(re.findall(r'\d+[A-Za-z]?', text))))


def platform_evidence(root):
	"""Use operator observations; Track is scoped to station AND line, never global.

	END nodes have no platform. Infer only from unanimous platform observations
	for that station/line/Track in other responses, retaining source hashes.
	"""
	meta_path = root / 'data/mtr_api/inventory.json'
	meta = json.loads(meta_path.read_text())['HR']['metadata']
	lines = {str(l['ID']): l['alias'] for l in meta['lines']}
	files = sorted((root / 'data/mtr_api/raw').glob('HR_*.json'))
	if not files:
		raise ValueError('Missing cached official MTR responses for platform matching')
	tracks, departures, arrivals = defaultdict(set), defaultdict(set), defaultdict(set)
	observations, hashes = [], {}
	for file in files:
		content = file.read_bytes()
		raw = json.loads(content)
		hashes[file.name] = hashlib.sha256(content).hexdigest()
		if raw.get('status') != 'ok':
			continue
		for route in raw.get('response', {}).get('routes', []):
			if route.get('special') or route.get('rules') or route.get('routeStatus'):
				continue
			nodes = route.get('path') or []
			if any(n.get('stationStatus') or n.get('linkStatus') for n in nodes):
				continue
			for i, node in enumerate(nodes):
				line = lines.get(str(node.get('lineID')))
				if not line or node.get('Track') is None:
					continue
				sid = f"RAIL:MTR:{node['ID']}:{line}"
				track = (sid, str(node['Track']))
				codes = platform_codes(node.get('platform'))
				if codes:
					tracks[track].add(codes)
				if i + 1 < len(nodes) and codes and node.get('linkType') in ('RIDE', 'INTERCHANGE'):
					departures[(sid, str(nodes[i + 1]['ID']))].add(codes)
				if i and node.get('linkType') == 'END':
					observations.append((sid, str(nodes[i - 1]['ID']), track))
	for sid, previous, track in observations:
		values = tracks.get(track, set())
		if len(values) == 1:
			arrivals[(sid, previous)].update(values)
		else:
			# An unresolved observation prevents silently selecting other evidence.
			arrivals[(sid, previous)].add(())
	def resolve(values):
		return next(iter(values)) if len(values) == 1 and () not in values else ()
	return ({k: resolve(v) for k, v in departures.items()},
		{k: resolve(v) for k, v in arrivals.items()},
		dict(files=hashes, inventory_sha256=hashlib.sha256(meta_path.read_bytes()).hexdigest(),
			policy='Departure: unanimous explicit platforms for station/line/next station. Arrival: unanimous station/line/Track platform observations, then previous-station agreement.',
			departure_pairs=len(departures), arrival_pairs=len(arrivals),
			unresolved_departure_pairs=sum(not resolve(v) for v in departures.values()),
			unresolved_arrival_pairs=sum(not resolve(v) for v in arrivals.values())))


def choose_entrance(stop, amenities, horizontal):
	"""Exact exit code plus position; ground-level labels resolve duplicate floors."""
	match = re.search(r'-([A-Z]\d*) (?:Access|Exit)(?: ·|$)', stop['stop_name'])
	code = match.group(1) if match else None
	if not code:
		if stop.get('source_subcat') == 'MTRLIF':
			lifts = [f for f in amenities if f['properties'].get('amenity_category') == 'elevator'
				and re.search(r'\b(ground|street)\b', f['properties'].get('level_name_en') or '', re.I)
				and horizontal((float(stop['stop_lon']),float(stop['stop_lat'])),f['geometry']['coordinates']) <= 10]
			if len(lifts) == 1:
				return lifts[0], None
		return None, 'Entrance has no published exit code'
	candidates = [f for f in amenities if f['properties'].get('amenity_category') == 'entry'
		and re.fullmatch(r'Exit\s+' + re.escape(code), unicodedata.normalize('NFKC', f['properties'].get('amenity_name_en') or '').strip(), re.I)]
	if len(candidates) > 1:
		ground = [f for f in candidates if re.search(r'\b(ground|street)\b', f['properties'].get('level_name_en') or '', re.I)]
		if ground:
			candidates = ground
		else:
			named_level = [f for f in candidates if re.match(r'^Exit\s+'+re.escape(code)+r'(?:\s|$)', f['properties'].get('level_name_en') or '', re.I)]
			if named_level:
				candidates = named_level
		if len(candidates) > 1 and max(f['geometry']['coordinates'][2] for f in candidates) - min(f['geometry']['coordinates'][2] for f in candidates) <= .005:
			# Multiple doors for the same exit on the same physical level: use the closest to the independent reference.
			candidates = [min(candidates, key=lambda f: horizontal((float(stop['stop_lon']),float(stop['stop_lat'])),f['geometry']['coordinates']))]
	if len(candidates) != 1:
		return None, 'Exit code is missing or ambiguous across map floors: ' + code
	feature = candidates[0]
	distance = horizontal((float(stop['stop_lon']), float(stop['stop_lat'])), feature['geometry']['coordinates'])
	if distance > 40:
		return None, f'Exit {code} position disagreement: {distance:.1f} m'
	return feature, None


def collapse_lift_splits(edges):
	"""Collapse degree-two points inside one continuous source lift shaft.

	Do not collapse a landing with any non-lift branch or differing direction.
	Preserves every source segment ID and charges one wait for one lift ride.
	"""
	incident = defaultdict(list)
	for edge in edges:
		for node in (edge['a'], edge['b']):
			incident[node].append(edge)
	removed, replacements = set(), []
	for edge in edges:
		if edge['id'] in removed or edge['mode'] != 5 or not edge['both']:
			continue
		chain, ends = [edge], [edge['a'], edge['b']]
		for side in (0, 1):
			while len(incident[ends[side]]) == 2:
				next_edges = [e for e in incident[ends[side]] if e not in chain]
				if len(next_edges) != 1:
					break
				next_edge = next_edges[0]
				if next_edge['mode'] != 5 or not next_edge['both'] or next_edge['id'] in removed:
					break
				chain.append(next_edge)
				ends[side] = next_edge['b'] if next_edge['a'] == ends[side] else next_edge['a']
		if len(chain) > 1 and ends[0] != ends[1]:
			removed.update(e['id'] for e in chain)
			combined = dict(edge, id='lift:' + ':'.join(sorted(e['id'] for e in chain)),
				a=ends[0], b=ends[1], source_ids=sorted({e['source_id'] for e in chain}),
				length=sum(e['length'] for e in chain), seconds=sum(e['seconds'] for e in chain) - 20 * (len(chain) - 1))
			replacements.append(combined)
	return [e for e in edges if e['id'] not in removed] + replacements


def map_platform_codes(name):
	match = re.match(r'^Platform\s*(\d+[A-Za-z]?(?:\s*[,/&]\s*\d+[A-Za-z]?)*)', name or '', re.I)
	return platform_codes(match.group(1)) if match else ()


def coincident_vertices(coordinates, horizontal):
	"""Join <=1 cm XY / <=5 mm Z endpoint discrepancies, never different floors.

	Each point must be close to its representative; transitive chains cannot grow
	the tolerance. Deterministic order makes the same cache reproduce identically.
	"""
	buckets, result = defaultdict(list), {}
	for c in sorted(set(coordinates)):
		key = (math.floor(c[0]*102900/.01), math.floor(c[1]*111195/.01), math.floor(c[2]/.005))
		near = [p for dx in (-1,0,1) for dy in (-1,0,1) for dz in (-1,0,1)
			for p in buckets[(key[0]+dx,key[1]+dy,key[2]+dz)]
			if abs(p[2]-c[2]) <= .005 and horizontal(p,c) <= .01]
		if near:
			result[c] = min(near, key=lambda p:(horizontal(p,c),p))
		else:
			result[c] = c
			buckets[key].append(c)
	return result
