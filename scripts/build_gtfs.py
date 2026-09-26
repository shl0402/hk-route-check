"""Offline HK GTFS builder. Strict by default; explicit --allow-estimates for prototypes.
Never upgrades unproven legacy durations into verified measurements.
"""

import argparse, csv, io, json, math, re, zipfile, hashlib, shutil
from urllib.parse import quote
from collections import defaultdict, Counter
from pathlib import Path
from datetime import date, timedelta
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data/raw/2026-09-17'
INPUT = ROOT / 'data/user_inputs/mtr'
OUT = ROOT / 'data/generated'


def read_csv(path):
	with path.open(encoding='utf-8-sig', newline='') as f:
		return list(csv.DictReader(f))


def distance(a, b):
	p, q = map(math.radians, (a[0], b[0]))
	dp = q - p
	dl = math.radians(b[1] - a[1])
	return (
		6371000
		* 2
		* math.asin(
			min(
				1,
				math.sqrt(
					math.sin(dp / 2) ** 2
					+ math.cos(p) * math.cos(q) * math.sin(dl / 2) ** 2
				),
			)
		)
	)


def clock(seconds):
	seconds = round(seconds)
	return f'{seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}'


def seconds(text):
	h, m, s = map(int, text.split(':'))
	return h * 3600 + m * 60 + s


def write_table(rows, fields):
	f = io.StringIO(newline='')
	w = csv.DictWriter(f, fieldnames=fields, lineterminator='\n')
	w.writeheader()
	w.writerows(rows)
	return f.getvalue()


def source(path):
	return dict(
		path=str(path.relative_to(ROOT)),
		sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
	)


def network():
	result = defaultdict(list)
	for system, fn, idcol in [
		('MTR', 'mtr_lines_and_stations.csv', 'Station ID'),
		('LRT', 'light_rail_routes_and_stops.csv', 'Stop ID'),
	]:
		for row in read_csv(INPUT / fn):
			if not row['Sequence'] or not row['Line Code']:
				continue
			row.update(system=system, id=row[idcol], sid=f"RAIL:{system}:{row[idcol]}")
			result[(system, row['Line Code'], row['Direction'])].append(row)
	for rows in result.values():
		rows.sort(key=lambda r: float(r['Sequence']))
	return result


def geography(net, report):
	cache = (
		json.loads((INPUT / 'geo_coords_cache.json').read_text())
		if (INPUT / 'geo_coords_cache.json').exists()
		else {}
	)
	official = {}
	for system, filename in [
		('MTR', 'heavyRailDetails.json'),
		('LRT', 'lightRailDetails.json'),
	]:
		path = ROOT / 'data/mtr_api_audit/2026-09-21' / filename
		if path.exists():
			for station in json.loads(path.read_text())['stations']:
				if station.get('coordinate'):
					lat, lon = map(float, station['coordinate'].split(','))
					official[f"RAIL:{system}:{station['ID']}"] = (lat, lon)
	features = json.loads((ROOT / 'data/derived/osm_rail_features.json').read_text())
	points = {}
	evidence = {}
	for rows in net.values():
		for r in rows:
			if r['sid'] in points:
				continue
			name = r['English Name']
			v = cache.get(name)
			candidates = [
				f
				for f in features
				if f['tags'].get('name:en', '').casefold() == name.casefold()
				and f['tags'].get('railway') != 'subway_entrance'
				and (
					(r['system'] == 'LRT')
					== (
						f['tags'].get('station') == 'light_rail'
						or f['tags'].get('light_rail') == 'yes'
						or f['tags'].get('railway') == 'tram_stop'
					)
				)
			]
			# Prefer actual named station nodes, then the numeric operator reference.
			candidates.sort(
				key=lambda f: (
					f['tags'].get('ref') != r['id'],
					not f['id'].startswith('node/'),
					f['id'],
				)
			)
			osm = candidates[0] if candidates else None
			if r['sid'] in official:
				pt = official[r['sid']]
				if not (22.1 < pt[0] < 22.6 and 113.8 < pt[1] < 114.5):
					raise ValueError(f'Invalid MTR coordinates for {name}')
				evidence[r['sid']] = dict(
					name=name,
					kind='MTR_journey_planner_station_coordinate',
					source='https://www.mtr.com.hk/en/customer/jp/index.php',
				)
			elif v:
				pt = (float(v['lat']), float(v['lon']))
				if not (22.1 < pt[0] < 22.6 and 113.8 < pt[1] < 114.5) or pt == (
					22.3,
					114.1,
				):
					raise ValueError(f'Invalid/placeholder coordinates for {name}')
				evidence[r['sid']] = dict(
					name=name, kind='user_geocode_cache', verified=False
				)
				if osm:
					gap = distance(pt, (osm['lat'], osm['lon']))
					evidence[r['sid']].update(
						osm_match=osm['id'], difference_metres=round(gap)
					)
					if gap > 500:
						report['coordinate_disagreements'].append(
							dict(
								name=name,
								metres=round(gap),
								cache=pt,
								osm=[osm['lat'], osm['lon']],
							)
						)
						pt = (osm['lat'], osm['lon'])
						evidence[r['sid']][
							'kind'
						] = 'OSM_named_station_replaces_discrepant_geocode'
			elif osm:
				pt = (osm['lat'], osm['lon'])
				evidence[r['sid']] = dict(
					name=name, kind='OSM_named_station', osm_match=osm['id']
				)
			else:
				raise ValueError(
					f'Missing coordinates for {r["sid"]} {name}; no fallback permitted'
				)
			points[r['sid']] = pt
	entrances = [f for f in features if f['tags'].get('railway') == 'subway_entrance']
	report['entrances'] = dict(
		count=len(entrances),
		with_ref=sum(bool(f['tags'].get('ref')) for f in entrances),
		gtfs_pathways_available=False,
	)
	report['coordinates'] = evidence
	return points


def published_frequencies():
	path = RAW / 'mtr/service_hours.html'
	soup = BeautifulSoup(path.read_text(), 'html.parser')
	out = {}
	for tr in soup.select('table tr'):
		cells = [c.get_text(' ', strip=True) for c in tr.find_all(['td', 'th'])]
		if len(cells) == 6 and any(re.search(r'\d', x) for x in cells[1:]):
			out[cells[0]] = cells[1:]
	if 'Island Line' not in out:
		raise ValueError('MTR frequency table format changed')
	return out


def frequency_for(key, table):
	system, line, direction = key
	labels = {
		'ISL': 'Island Line',
		'TWL': 'Tsuen Wan Line',
		'KTL': 'Ho Man Tin-Whampoa',
		'SIL': 'South Island Line',
		'TCL': 'Hong Kong-Tung Chung',
		'TML': 'Tuen Ma Line',
		'AEL': 'Airport Express',
	}
	if system == 'LRT':
		label = next(
			(
				k
				for k in table
				if k.startswith(f'Route {line}') and k.split()[1] == line
			),
			None,
		)
	elif line == 'DRL':
		label = next(k for k in table if k.startswith('Disneyland Resort Line'))
	elif line == 'EAL':
		label = 'Admiralty-Lok Ma Chau' if 'LMC' in direction else 'Admiralty-Lo Wu'
	elif line == 'TKL':
		label = (
			'Tiu Keng Leng-LOHAS Park' if 'TKS' in direction else 'North Point-Po Lam'
		)
	else:
		label = labels.get(line)
	if label not in table:
		raise ValueError(f'No published headways for {key}')
	# One conservative frequency for this experimental feed; no invented peak boundaries.
	values = [
		float(n)
		for cell in table[label]
		for n in re.findall(r'\d+(?:\.\d+)?', cell.split('(')[0])
	]
	night = (
		25 if system == 'LRT' else 21 if line == 'EAL' else 20 if line == 'DRL' else 12
	)
	return round(max(*values, night) * 60), dict(
		source='mtr/service_hours.html',
		label=label,
		published_cells=table[label],
		policy='maximum published range including early/late service note, applied all day; conservative estimate',
	)


def operating_hours(rows, line):
	path = ROOT / 'data/derived/mtr_service_hours' / f'{rows[0]["id"]}.html'
	if rows[0]['system'] == 'LRT' or not path.exists():
		return None
	soup = BeautifulSoup(path.read_text(), 'html.parser')
	for heading in soup.select('h2.trainLine'):
		if line not in heading.get('class', []):
			continue
		table = heading.find_next('table')
		for tr in table.select('tr'):
			cells = tr.find_all('td')
			if len(cells) != 3:
				continue
			classes = ' '.join(
				c for el in cells[0].find_all(class_=True) for c in el.get('class', [])
			)
			if not re.search(
				r'js_station_' + re.escape(rows[-1]['id']) + r'(?:_|\b)', classes
			):
				continue
			a, b = [c.get_text('', strip=True) for c in cells[1:]]
			if not (re.fullmatch(r'\d{4}', a) and re.fullmatch(r'\d{4}', b)):
				continue
			start = int(a[:2]) * 3600 + int(a[2:]) * 60
			end = int(b[:2]) * 3600 + int(b[2:]) * 60
			if end < start:
				end += 86400
			return (
				start,
				end,
				dict(
					source=str(path.relative_to(ROOT)),
					kind='published_first_last_train',
					destination=rows[-1]['id'],
				),
			)
	return None


def validate(tables):
	errors = []
	for table, col in [
		('stops.txt', 'stop_id'),
		('routes.txt', 'route_id'),
		('trips.txt', 'trip_id'),
		('agency.txt', 'agency_id'),
	]:
		ids = [r[col] for r in tables[table]]
		if len(ids) != len(set(ids)):
			errors.append(f'Duplicate {col}')
	stops = {r['stop_id'] for r in tables['stops.txt']}
	routes = {r['route_id'] for r in tables['routes.txt']}
	trips = {r['trip_id'] for r in tables['trips.txt']}
	services = {
		r['service_id']
		for name in ('calendar.txt', 'calendar_dates.txt')
		for r in tables.get(name, [])
	}
	for r in tables['trips.txt']:
		if r['route_id'] not in routes or r['service_id'] not in services:
			errors.append(f'Broken trip reference {r["trip_id"]}')
	grouped = defaultdict(list)
	for r in tables['stop_times.txt']:
		if r['stop_id'] not in stops or r['trip_id'] not in trips:
			errors.append('Broken stop-time reference')
		grouped[r['trip_id']].append(r)
	for trip, rows in grouped.items():
		seq = [int(r['stop_sequence']) for r in rows]
		if len(seq) != len(set(seq)):
			errors.append(f'Duplicate sequence {trip}')
		last = -1
		for r in sorted(rows, key=lambda r: int(r['stop_sequence'])):
			if not r.get('arrival_time') or not r.get('departure_time'):
				continue
			a, b = seconds(r['arrival_time']), seconds(r['departure_time'])
			if a < last or b < a:
				errors.append(f'Nonmonotonic times {trip}')
			last = b
	for r in tables.get('frequencies.txt', []):
		if (
			r['trip_id'] not in trips
			or int(r['headway_secs']) <= 0
			or seconds(r['start_time']) >= seconds(r['end_time'])
		):
			errors.append('Invalid frequency')
	return sorted(set(errors))


def build(allow_estimates=False, start_date=None, days=30):
	OUT.mkdir(parents=True, exist_ok=True)
	if not 1 <= days <= 366:
		raise ValueError('days must be 1..366')
	start = date.fromisoformat(start_date) if start_date else date.today()
	end = start + timedelta(days=days - 1)
	report = dict(
		status='auditing',
		coordinate_disagreements=[],
		assumptions=[],
		patterns={},
		sources=[
			source(INPUT / f)
			for f in [
				'geo_coords_cache.json',
				'travel_time_cache.json',
				'mtr_lines_and_stations.csv',
				'light_rail_routes_and_stops.csv',
			]
			if (INPUT / f).exists()
		]
		+ [source(RAW / 'mtr/service_hours.html'), source(RAW / 'td/gtfs.zip')],
	)
	net = network()
	points = geography(net, report)
	freqs = published_frequencies()
	legacy = (
		json.loads((INPUT / 'travel_time_cache.json').read_text())
		if (INPUT / 'travel_time_cache.json').exists()
		else {}
	)
	report['legacy_cache'] = dict(
		entries=len(legacy),
		provenance='unverified: raw responses, timestamps and fallback flags absent',
		id_collisions=[
			'100: MTR Tai Shui Hang / LRT Siu Hong',
			'120: MTR Tuen Mun / LRT Ching Chung',
		],
	)
	# Optional reviewed overrides are the only way to classify a running-time input as verified.
	override_path = ROOT / 'data/user_inputs/mtr/verified_segments.json'
	overrides = json.loads(override_path.read_text()) if override_path.exists() else {}
	from mtr_api_patterns import MtrApiPatterns

	mtr_api = MtrApiPatterns(ROOT)
	rail = defaultdict(list)
	rail['agency.txt'] = [
		dict(
			agency_id='RAIL:MTR',
			agency_name='MTR / Light Rail (experimental estimates)',
			agency_url='https://www.mtr.com.hk',
			agency_timezone='Asia/Hong_Kong',
		)
	]
	rail['calendar.txt'] = [
		dict(
			service_id='RAIL:DAILY',
			**{
				x: '1'
				for x in [
					'monday',
					'tuesday',
					'wednesday',
					'thursday',
					'friday',
					'saturday',
					'sunday',
				]
			},
			start_date=start.strftime('%Y%m%d'),
			end_date=end.strftime('%Y%m%d'),
		)
	]
	stations = {r['sid']: r for rows in net.values() for r in rows}
	platforms = set()
	routes = set()
	members = defaultdict(set)
	for sid, r in stations.items():
		lat, lon = points[sid]
		rail['stops.txt'].append(
			dict(
				stop_id=sid,
				stop_name=r['English Name'],
				stop_lat=lat,
				stop_lon=lon,
				location_type='1',
			)
		)
	for key, rows in net.items():
		system, line, direction = key
		rid = f'RAIL:{system}:{line}'
		tid = f'{rid}:{direction}'
		if rid not in routes:
			rail['routes.txt'].append(
				dict(
					route_id=rid,
					agency_id='RAIL:MTR',
					route_short_name=line,
					route_long_name=f'{line} {system}',
					route_type='0' if system == 'LRT' else '1',
				)
			)
			routes.add(rid)
		rail['trips.txt'].append(
			dict(
				route_id=rid,
				service_id='RAIL:DAILY',
				trip_id=tid,
				trip_headsign=rows[-1]['English Name'],
				direction_id=(
					'0' if direction.endswith('DT') or direction == '1' else '1'
				),
			)
		)
		headway, frequency_source = frequency_for(key, freqs)
		hours = operating_hours(rows, line)
		if not hours:
			hours = (
				6 * 3600,
				24 * 3600,
				dict(
					kind='ASSUMPTION',
					reason='No matched first/last timetable: provisional 06:00–24:00',
				),
			)
			report['assumptions'].append(f'{tid}: provisional service hours')
		first, last, hours_source = hours
		rail['frequencies.txt'].append(
			dict(
				trip_id=tid,
				start_time=clock(first),
				end_time=clock(last),
				headway_secs=headway,
				exact_times='0',
			)
		)
		report['patterns'][tid] = dict(
			frequency=frequency_source, service_hours=hours_source, segments=[]
		)
		report['assumptions'].append(
			f'{tid}: single conservative headway instead of actual changing service intervals'
		)
		api_timing = (
			mtr_api.timing(line, tuple(r['id'] for r in rows))
			if system == 'MTR'
			else None
		)
		elapsed = 0
		for i, r in enumerate(rows):
			# Logical line boarding points, NOT claims of measured platform positions.
			pid = f'{r["sid"]}:{line}'
			members[r['sid']].add(pid)
			if pid not in platforms:
				lat, lon = points[r['sid']]
				rail['stops.txt'].append(
					dict(
						stop_id=pid,
						stop_name=f'{r["English Name"]} ({line})',
						stop_lat=lat,
						stop_lon=lon,
						location_type='0',
						parent_station=r['sid'],
					)
				)
				platforms.add(pid)
			if i:
				prev = rows[i - 1]
				segkey = f'{system}:{line}:{prev["id"]}>{r["id"]}'
				proof = overrides.get(segkey)
				if api_timing:
					evidence = api_timing['segments'][i - 1]
					duration = evidence['seconds']
					kind = evidence['kind']
				elif proof:
					if (
						not proof.get('source')
						or proof.get('semantics') != 'in_vehicle_seconds'
						or not isinstance(proof.get('seconds'), (int, float))
						or proof['seconds'] <= 0
					):
						raise ValueError(f'Invalid verified override: {segkey}')
					duration = round(proof['seconds'])
					kind = 'reviewed_override'
					evidence = proof
				else:
					# Legacy integer times may include waiting; do NOT add a frequency wait to them.
					# Use them only as an audit comparison, never as in-vehicle measurements.
					metres = distance(points[prev['sid']], points[r['sid']])
					assert system == 'LRT', 'MTR must use its cached API path'
					speed = 18
					duration = max(45, round(metres * 1.25 / (speed / 3.6) + 20))
					kind = 'ASSUMPTION_distance_speed_model'
					evidence = dict(
						straight_line_metres=round(metres),
						detour_factor=1.25,
						speed_kmh=speed,
						dwell_seconds=20,
						legacy_cache_value=legacy.get(f'{prev["id"]}_{r["id"]}'),
						legacy_value_used=False,
					)
					report['assumptions'].append(
						f'{segkey}: running time unavailable; distance/speed estimate'
					)
				elapsed += duration
				report['patterns'][tid]['segments'].append(
					dict(key=segkey, seconds=duration, kind=kind, evidence=evidence)
				)
			rail['stop_times.txt'].append(
				dict(
					trip_id=tid,
					arrival_time=clock(elapsed),
					departure_time=clock(elapsed),
					stop_id=pid,
					stop_sequence=i + 1,
					timepoint='0',
				)
			)
	# Explicit provisional transfers between logical line stops at a shared station.
	# No nearest-station links and no invented entrance/pathway graph.
	for sid, pids in members.items():
		for a in sorted(pids):
			for b in sorted(pids):
				if a != b:
					rail['transfers.txt'].append(
						dict(
							from_stop_id=a,
							to_stop_id=b,
							transfer_type='2',
							min_transfer_time='300',
						)
					)
	if rail['transfers.txt']:
		report['assumptions'].append(
			'Within-station line transfers use provisional 300 seconds; physical pathway times are missing'
		)
	# MTR AEL/TCL use separate official IDs for the same named interchange.
	byname = defaultdict(list)
	for sid, r in stations.items():
		byname[r['English Name']].append(sid)
	for name, sids in byname.items():
		for a in sids:
			for b in sids:
				if a == b or distance(points[a], points[b]) > 500:
					continue
				for pa in members[a]:
					for pb in members[b]:
						rail['transfers.txt'].append(
							dict(
								from_stop_id=pa,
								to_stop_id=pb,
								transfer_type='2',
								min_transfer_time='300',
							)
						)
	report['assumptions'].append(
		'Nearby same-name MTR/LRT or AEL/TCL interchange links use provisional 300 seconds; gate/fare restrictions are not modeled'
	)
	report['missing'] = [
		'Verified in-vehicle running times (legacy journey totals may include waits)',
		'Time-specific headways and special-day/branch service patterns',
		'Measured platform/entrance transfer graph and walking times',
		'Some first/last train times, especially Light Rail',
		'Full-day exact MTR train departure timetable',
	]
	report['rail_rows'] = {k: len(v) for k, v in rail.items()}
	if not allow_estimates:
		report['status'] = 'blocked_by_unverified_inputs'
		(OUT / 'strict_quality_report.json').write_text(
			json.dumps(report, ensure_ascii=False, indent=2)
		)
		raise ValueError(
			'Strict build blocked: unverified running times/service/transfer assumptions. See data/generated/strict_quality_report.json. --allow-estimates generates an explicitly experimental feed.'
		)
	# Preserve government records and original schemas while appending namespaced rail.
	tables = {}
	fields = {}
	with zipfile.ZipFile(RAW / 'td/gtfs.zip') as z:
		for name in z.namelist():
			if not name.endswith('.txt'):
				continue
			reader = csv.DictReader(io.StringIO(z.read(name).decode('utf-8-sig')))
			fields[name] = list(reader.fieldnames or [])
			tables[name] = list(reader)
	for name, rows in rail.items():
		tables.setdefault(name, []).extend(rows)
		fields.setdefault(name, [])
		for row in rows:
			for col in row:
				if col not in fields[name]:
					fields[name].append(col)
	# Normalize syntactically invalid URLs already present in the TD source.
	report['source_normalizations'] = []
	for filename, column in [('agency.txt', 'agency_url'), ('routes.txt', 'route_url')]:
		for row in tables[filename]:
			original = row.get(column, '')
			fixed = quote(original.split('|')[0].strip(), safe=":/?#[]@!$&'()*+,;=%")
			if fixed != original:
				report['source_normalizations'].append(
					dict(file=filename, field=column, original=original, value=fixed)
				)
				row[column] = fixed
	errors = validate(tables)
	report['validation_errors'] = errors
	report['combined_rows'] = {k: len(v) for k, v in tables.items()}
	report['government_timing_audit'] = dict(
		stop_time_rows=len(tables['stop_times.txt']) - len(rail['stop_times.txt']),
		missing_arrivals=sum(
			not r.get('arrival_time') for r in tables['stop_times.txt']
		),
		missing_departures=sum(
			not r.get('departure_time') for r in tables['stop_times.txt']
		),
		timepoint_counts=dict(
			Counter(
				r.get('timepoint', '')
				for r in tables['stop_times.txt']
				if not r['trip_id'].startswith('RAIL:')
			)
		),
	)
	if errors:
		report['status'] = 'validation_failed'
		(OUT / 'quality_report.json').write_text(
			json.dumps(report, ensure_ascii=False, indent=2)
		)
		raise ValueError(f'{len(errors)} structural errors; no ZIP written')
	target = OUT / 'hk-transit-EXPERIMENTAL.gtfs.zip'
	tmp = target.with_suffix('.tmp')
	with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
		for name, rows in tables.items():
			z.writestr(name, write_table(rows, fields[name]))
	tmp.replace(target)
	report['status'] = 'experimental_estimates_not_verified_timetable'
	report['output'] = source(target)
	report['date_range'] = [str(start), str(end)]
	(OUT / 'quality_report.json').write_text(
		json.dumps(report, ensure_ascii=False, indent=2)
	)
	print(
		f'EXPERIMENTAL GTFS: {target}\nStructural checks passed. Rail running times, headways and transfers contain explicit estimates.\nReport: {OUT/"quality_report.json"}'
	)
	return target


def main():
	p = argparse.ArgumentParser(description=__doc__)
	p.add_argument('--allow-estimates', action='store_true')
	p.add_argument('--start-date')
	p.add_argument('--days', type=int, default=30)
	a = p.parse_args()
	try:
		build(a.allow_estimates, a.start_date, a.days)
	except (ValueError, FileNotFoundError) as e:
		p.exit(2, str(e) + '\n')


if __name__ == '__main__':
	main()
