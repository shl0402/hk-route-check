"""Experimental full-journey GTFS connections. Internal changes are API-inclusive.
These are routing connections, not a claim that one train traverses all lines.
"""

import bisect, collections, copy, csv, datetime, hashlib, io, json, zipfile
from compile_mtr_od import seconds, clock, encode


def merged(windows):
	out = []
	for a, b in sorted(windows):
		if out and a <= out[-1][1]:
			out[-1] = (out[-1][0], max(b, out[-1][1]))
		else:
			out.append((a, b))
	return out


def intersect_windows(left, right):
	"""Intersect sorted disjoint half-open windows without a quadratic product."""
	ends = [b for _, b in right]
	out = []
	for a, b in left:
		i = bisect.bisect_right(ends, a)
		while i < len(right) and right[i][0] < b:
			c, d = right[i]
			out.append((max(a, c), min(b, d)))
			i += 1
	return merged(out)


def journey_variants(routes, aliases):
	"""Expand the same-line TKO change explicitly described in the MTR response.

	The API path omits this conditional change. Keep the operator's complete OD
	time unchanged; splitting here selects service availability, not ride costs.
	Both variants must still pass each section's dated timetable checks.
	"""
	for index, route in enumerate(routes):
		yield str(index), route
		messages = [m.get('msgText', '') for m in route.get('messages') or [] if isinstance(m, dict)]
		note = next((m for m in messages if 'During non-peak hours' in m and
			'interchange at Tseung Kwan O' in m), None)
		nodes = route.get('path') or []
		if not note or not any(str(n.get('ID')) == '57' for n in nodes):
			continue
		for i in range(1, len(nodes) - 1):
			if (str(nodes[i].get('ID')) == '50' and nodes[i].get('linkType') == 'RIDE'
				and all(aliases.get(str(n.get('lineID'))) == 'TKL' for n in nodes[i-1:i+1])):
				variant = copy.deepcopy(route)
				for n in variant['path'][:i]:
					if aliases.get(str(n.get('lineID'))) == 'TKL' and n.get('linkText'):
						n['linkText'] = n['linkText'].replace('towards North Point', 'towards Tseung Kwan O').replace('towards LOHAS Park', 'towards Tseung Kwan O')
				variant['path'][i]['linkType'] = 'INTERCHANGE'
				variant['path'][i]['linkText'] = note.strip()
				variant['timetable_variant'] = 'TKO same-line change from MTR service note'
				yield f'{index}:TKO', variant
				break


def compile_interchanges(root, path):
	with zipfile.ZipFile(path) as z:

		def read(name):
			return (
				list(csv.DictReader(io.StringIO(z.read(name).decode('utf-8-sig'))))
				if name in z.namelist()
				else []
			)

		trips = read('trips.txt')
		freq = read('frequencies.txt')
		stops = read('stops.txt')
		dates = read('calendar_dates.txt')
		transfers = read('transfers.txt')
		proof = json.loads(z.read('mtr_api_provenance.json'))
		journeys = proof['od_journeys']
		ods = proof['od_trips']
		bytid = {t['trip_id']: t for t in trips}
		byfreq = collections.defaultdict(list)
		for f in freq:
			byfreq[f['trip_id']].append(f)
		active = collections.defaultdict(set)
		for c in read('calendar.txt'):
			day = datetime.datetime.strptime(c['start_date'], '%Y%m%d').date()
			end = datetime.datetime.strptime(c['end_date'], '%Y%m%d').date()
			while day <= end:
				if (
					c[
						[
							'monday',
							'tuesday',
							'wednesday',
							'thursday',
							'friday',
							'saturday',
							'sunday',
						][day.weekday()]
					]
					== '1'
				):
					active[c['service_id']].add(day.strftime('%Y%m%d'))
				day += datetime.timedelta(days=1)
		for d in dates:
			if d['exception_type'] == '1':
				active[d['service_id']].add(d['date'])
			else:
				active[d['service_id']].discard(d['date'])
		# Exact same-line connections give direction/path-specific availability.
		bands = collections.defaultdict(lambda: collections.defaultdict(list))
		availability = {}
		scheduled = {}
		for r in read('stop_times.txt'):
			if r['trip_id'] in ods and r['stop_sequence'] == '1':
				scheduled[r['trip_id']] = seconds(r['departure_time'])
		for tid, o in ods.items():
			trip = bytid[tid]
			for day in active[trip['service_id']]:
				for f in byfreq[tid]:
					bands[o['journey']][day].append(
						(
							seconds(f['start_time']),
							seconds(f['end_time']),
							int(f['headway_secs']),
							f.get('exact_times', '0'),
							o['original_trip'],
						)
					)
				if not byfreq[tid]:
					bands[o['journey']][day].append(
						(scheduled[tid], scheduled[tid] + 1, 0, '1', o['original_trip'])
					)
		for key, days in bands.items():
			for day, rows in days.items():
				days[day] = sorted(set(rows))
			availability[key] = {
				day: merged((a, b) for a, b, *_ in rows) for day, rows in days.items()
			}
		metadata = json.loads((root / 'data/mtr_api/inventory.json').read_text())['HR'][
			'metadata'
		]
		aliases = {str(l['ID']): l['alias'] for l in metadata['lines']}
		stopmap = {s['stop_id']: s for s in stops}
		newrows = []
		newtrips = []
		newfreq = []
		newdates = []
		services = {}
		rejected = collections.Counter()
		accepted = 0
		for file in sorted((root / 'data/mtr_api/raw').glob('HR_*.json')):
			raw = json.loads(file.read_text())
			for ri, route in journey_variants(raw.get('response', {}).get('routes', []), aliases):
				nodes = route.get('path') or []
				if not any(n.get('linkType') == 'INTERCHANGE' for n in nodes):
					continue
				if (
					route.get('special')
					or route.get('rules')
					or route.get('routeStatus')
					or any(n.get('stationStatus') or n.get('linkStatus') for n in nodes)
				):
					rejected['conditional/status-marked path'] += 1
					continue
				if (
					not nodes
					or nodes[-1].get('linkType') != 'END'
					or any(
						n.get('linkType') not in ('RIDE', 'INTERCHANGE')
						for n in nodes[:-1]
					)
				):
					rejected['unsupported walking or path type'] += 1
					continue
				try:
					times = [round(float(n['time']) * 60) for n in nodes]
					total = round(float(route['time']) * 60)
					ids = [str(int(n['ID'])) for n in nodes]
					lines = [aliases[str(n['lineID'])] for n in nodes]
					if (
						times[0] != 0
						or times[-1] != total
						or any(b <= a for a, b in zip(times, times[1:]))
					):
						raise ValueError()
					if (ids[0], ids[-1]) != (
						str(raw['origin']),
						str(raw['destination']),
					):
						raise ValueError()
				except (ValueError, KeyError, TypeError):
					rejected['invalid cumulative path'] += 1
					continue
				boundaries = (
					[0]
					+ [
						i
						for i, n in enumerate(nodes)
						if n.get('linkType') == 'INTERCHANGE'
					]
					+ [len(nodes) - 1]
				)
				sections = [
					(lines[a] + ':' + '>'.join(ids[a : b + 1]), a)
					for a, b in zip(boundaries, boundaries[1:])
				]
				if any(key not in bands for key, _ in sections):
					rejected['no matching line/path timetable'] += 1
					continue
				stopids = [f'RAIL:MTR:{id}:{line}' for id, line in zip(ids, lines)]
				if any(s not in stopmap for s in stopids):
					rejected['missing stop'] += 1
					continue
				# Restrict every section to its own dated timetable window. Internal
				# waits are already part of API total; never add another headway here.
				grouped = collections.defaultdict(set)
				parents = {}
				for day, first in bands[sections[0][0]].items():
					if any(day not in availability[key] for key, _ in sections[1:]):
						continue
					shifted_sections = [
						[(c - times[idx], d - times[idx]) for c, d in availability[key][day]]
						for key, idx in sections[1:]
					]
					for a, b, h, exact, parent in first:
						allowed = [(a, b)]
						for shifted in shifted_sections:
							allowed = intersect_windows(allowed, shifted)
							if not allowed:
								break
						for c, d in allowed:
							band = (c, d, h, exact)
							grouped[band].add(day)
							parents.setdefault(band, parent)
				if not grouped:
					rejected['no overlapping service dates/windows'] += 1
					continue
				key = f'JOURNEY:{ids[0]}>{ids[-1]}:{ri}'
				changes = [
					dict(
						station_id=ids[i],
						stop_id=stopids[i],
						line=lines[i],
						platform=nodes[i].get('platform'),
						instruction=nodes[i].get('linkText'),
						seconds=times[i],
					)
					for i in boundaries[1:-1]
				]
				journeys[key] = dict(
					seconds=times,
					api_total_seconds=total,
					path=ids,
					stop_ids=stopids,
					line=' → '.join(lines[i] for i in boundaries[:-1]),
					url=raw['url'],
					fetched_at=raw['fetched_at'],
					interchanges=changes,
					internal_transfers=len(changes),
					instructions=[
						dict(
							station_id=ids[i],
							stop_id=stopids[i],
							seconds=times[i],
							text=n['linkText'],
						)
						for i, n in enumerate(nodes)
						if n.get('linkText')
					],
					semantics='Whole cached platform-to-platform journey including internal changes; no added internal OTP wait.',
					messages=route.get('messages') or [],
					timetable_variant=route.get('timetable_variant'),
				)
				# A single GTFS trip can carry multiple non-overlapping frequency bands.
				# Keep identical dates together rather than duplicating its stop_times
				# for every time band. Overlapping bands get separate lanes.
				lanes = collections.defaultdict(list)
				scheduled_bands = []
				for (a, b, h, exact), days in sorted(grouped.items()):
					ds = tuple(sorted(days))
					parent = parents[(a, b, h, exact)]
					band = (a, b, h, exact, parent)
					if not h:
						scheduled_bands.append((ds, [band]))
						continue
					group = lanes[(ds, exact)]
					lane = next((lane for lane in group if lane[-1][1] <= a), None)
					if lane is None:
						lane = []
						group.append(lane)
					lane.append(band)
				bundles = scheduled_bands + [
					(ds, lane) for (ds, exact), group in lanes.items() for lane in group
				]
				for wi, (ds, bundle) in enumerate(bundles):
					a, b, h, exact, parent = bundle[0]
					if ds not in services:
						sid = (
							'MTRAPI:DATES:'
							+ hashlib.sha256(','.join(ds).encode()).hexdigest()[:16]
						)
						services[ds] = sid
						newdates.extend(
							dict(service_id=sid, date=d, exception_type='1') for d in ds
						)
					tid = f'MTRAPI:{ids[0]}:{ids[-1]}:{ri}:{wi}'
					template = bytid[parent]
					newtrips.append(
						dict(
							template,
							trip_id=tid,
							service_id=services[ds],
							trip_headsign=stopmap[stopids[-1]]['stop_name'],
						)
					)
					departure = 0 if h else a
					for sequence, sid, t, pick, drop in [
						(1, stopids[0], departure, '0', '1'),
						(2, stopids[-1], departure + total, '1', '0'),
					]:
						newrows.append(
							dict(
								trip_id=tid,
								arrival_time=clock(t),
								departure_time=clock(t),
								stop_id=sid,
								stop_sequence=str(sequence),
								pickup_type=pick,
								drop_off_type=drop,
								timepoint='0',
							)
						)
					if h:
						for c, d, headway, e, _ in bundle:
							newfreq.append(
								dict(
									trip_id=tid,
									start_time=clock(c),
									end_time=clock(d),
									headway_secs=str(headway),
									exact_times=e,
								)
							)
					ods[tid] = dict(
						original_trip=parent,
						journey=key,
						boarding_offset_seconds=1,
						frequency_parents=[
							dict(start=c, end=d, parent=p) for c, d, _, _, p in bundle
						],
					)
				accepted += 1
				if accepted % 1000 == 0:
					print(
						'Full interchange journeys:',
						accepted,
						'connections:',
						len(newtrips),
						flush=True,
					)
		# Forbid splitting a journey at the same station. The checker also
		# limits MTR-only searches to one connection and rejects split MTR blocks.
		# These restrictions are transit transfers, not access/egress walks.
		hrstops = [s['stop_id'] for s in stops if s['stop_id'].startswith('RAIL:MTR:')]
		transfers = [
			r
			for r in transfers
			if not (r['from_stop_id'] in hrstops and r['to_stop_id'] in hrstops)
		]
		transfers.extend(
			dict(from_stop_id=a, to_stop_id=b, transfer_type='3', min_transfer_time='')
			for a in hrstops
			for b in hrstops
			if a.split(':')[2] == b.split(':')[2]
		)
		proof.update(
			od_connections=len(ods),
			od_paths=len(journeys),
			interchange_paths=accepted,
			interchange_connections=len(newtrips),
			interchange_rejected=dict(rejected),
			mtr_timing_policy='Whole origin/destination API journey, including internal line changes. No added internal OTP transfer waits. Virtual routing connections; not through trains.',
		)
		tmp = path.with_suffix('.journey.tmp')
		with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as dest:
			replaced = {
				'trips.txt',
				'stop_times.txt',
				'frequencies.txt',
				'calendar_dates.txt',
				'transfers.txt',
				'mtr_api_provenance.json',
			}
			for name in z.namelist():
				if name not in replaced:
					import shutil

					with z.open(name) as f, dest.open(name, 'w') as o:
						shutil.copyfileobj(f, o)
			for name, rows in [
				('trips.txt', trips + newtrips),
				('frequencies.txt', freq + newfreq),
				('calendar_dates.txt', dates + newdates),
				('transfers.txt', transfers),
			]:
				dest.writestr(name, encode(rows, list(rows[0])))
			with z.open('stop_times.txt') as f, dest.open('stop_times.txt', 'w') as o:
				reader = csv.DictReader(io.TextIOWrapper(f, encoding='utf-8-sig'))
				stream = io.TextIOWrapper(o, encoding='utf-8', newline='')
				writer = csv.DictWriter(stream, reader.fieldnames, lineterminator='\n')
				writer.writeheader()
				writer.writerows(reader)
				writer.writerows(newrows)
				stream.flush()
				stream.detach()
			dest.writestr(
				'mtr_api_provenance.json', json.dumps(proof, ensure_ascii=False)
			)
	tmp.replace(path)
	report_path = root / 'data/mtr_api/merge_report.json'
	report = json.loads(report_path.read_text())
	report.update(
		{
			k: proof[k]
			for k in (
				'od_connections',
				'od_paths',
				'interchange_paths',
				'interchange_connections',
				'interchange_rejected',
				'mtr_timing_policy',
			)
		}
	)
	report_path.write_text(json.dumps(report, indent=2))
	print(
		'Whole-journey interchange compilation:',
		accepted,
		len(newtrips),
		dict(rejected),
		flush=True,
	)
