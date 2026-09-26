"""Compile exact cached MTR OD costs into GTFS before OTP routing.
Two-stop connections prevent OTP from boarding midway through an unrelated
longer API journey. Full station paths are retained for maps and provenance.
"""

import csv, io, json, zipfile, collections, hashlib
from mtr_api_patterns import MtrApiPatterns


def seconds(v):
	h, m, s = map(int, v.split(':'))
	return h * 3600 + m * 60 + s


def clock(v):
	return f'{v//3600:02}:{v//60%60:02}:{v%60:02}'


def encode(rows, fields):
	f = io.StringIO()
	w = csv.DictWriter(f, fields, lineterminator='\n')
	w.writeheader()
	w.writerows(rows)
	return f.getvalue().encode()


def compile_pairs(root, path):
	api = MtrApiPatterns(root)
	api_trips = {}
	journeys = {}
	rejected = {}
	cache = {}
	newtrips = []
	newfreq = []
	hrrows = {}
	parentrows = collections.defaultdict(list)
	with zipfile.ZipFile(path) as z:
		trips = list(
			csv.DictReader(io.TextIOWrapper(z.open('trips.txt'), encoding='utf-8-sig'))
		)
		freq = list(
			csv.DictReader(
				io.TextIOWrapper(z.open('frequencies.txt'), encoding='utf-8-sig')
			)
		)
		tripfields = list(trips[0])
		freqfields = list(freq[0])
		bytid = {t['trip_id']: t for t in trips}
		freqs = collections.defaultdict(list)
		for f in freq:
			freqs[f['trip_id']].append(f)
		for row in csv.DictReader(
			io.TextIOWrapper(z.open('stop_times.txt'), encoding='utf-8-sig')
		):
			if bytid[row['trip_id']]['route_id'].startswith('RAIL:MTR:'):
				parentrows[row['trip_id']].append(row)
		proof = json.loads(z.read('mtr_api_provenance.json'))
		for tid, rows in parentrows.items():
			rows.sort(key=lambda r: int(r['stop_sequence']))
			trip = bytid[tid]
			line = trip['route_id'].split(':')[-1]
			ids = tuple(str(int(r['stop_id'].split(':')[2])) for r in rows)
			base_departure = seconds(rows[0]['departure_time'])
			parent = proof['trips'][tid]
			for i in range(len(rows) - 1):
				for j in range(i + 1, len(rows)):
					path_ids = ids[i : j + 1]
					key = line + ':' + '>'.join(path_ids)
					if key not in cache:
						try:
							cache[key] = api.timing(line, path_ids)
						except ValueError as e:
							cache[key] = None
							rejected[key] = str(e)
					result = cache[key]
					if result is None:
						continue
					if key not in journeys:
						journeys[key] = {
							k: v for k, v in result.items() if k != 'segments'
						}
						journeys[key]['stop_ids'] = [
							r['stop_id'] for r in rows[i : j + 1]
						]
						journeys[key]['line'] = line
					newtid = (
						tid if i == 0 and j == len(rows) - 1 else tid + f':OD:{i}:{j}'
					)
					offset = parent['seconds'][i]
					dep = base_departure + offset
					arr = dep + result['api_total_seconds']
					# Only origin pickup and destination dropoff. No other boarding point can
					# inherit a subtracted duration from this OD query.
					a = dict(
						rows[i],
						trip_id=newtid,
						arrival_time=clock(dep),
						departure_time=clock(dep),
						stop_sequence='1',
						pickup_type='0',
						drop_off_type='1',
					)
					b = dict(
						rows[j],
						trip_id=newtid,
						arrival_time=clock(arr),
						departure_time=clock(arr),
						stop_sequence='2',
						pickup_type='1',
						drop_off_type='0',
						timepoint='0',
					)
					hrrows[newtid] = (a, b)
					newtrips.append(
						dict(
							trip,
							trip_id=newtid,
							trip_headsign=trip.get('trip_headsign', ''),
						)
					)
					for f in freqs[tid]:
						newfreq.append(
							dict(
								f,
								trip_id=newtid,
								start_time=clock(seconds(f['start_time']) + offset),
								end_time=clock(seconds(f['end_time']) + offset),
							)
						)
					api_trips[newtid] = {
						'original_trip': tid,
						'journey': key,
						'boarding_offset_seconds': offset,
					}
		fields = None
		tmp = path.with_suffix('.od.tmp')
		with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as dest:
			for name in z.namelist():
				if name in (
					'trips.txt',
					'frequencies.txt',
					'stop_times.txt',
					'mtr_api_provenance.json',
				):
					continue
				import shutil

				with z.open(name) as f, dest.open(name, 'w') as o:
					shutil.copyfileobj(f, o)
			dest.writestr(
				'trips.txt',
				encode(
					[t for t in trips if t['trip_id'] not in parentrows] + newtrips,
					tripfields,
				),
			)
			dest.writestr(
				'frequencies.txt',
				encode(
					[f for f in freq if f['trip_id'] not in parentrows] + newfreq,
					freqfields,
				),
			)
			with z.open('stop_times.txt') as f, dest.open('stop_times.txt', 'w') as o:
				reader = csv.DictReader(io.TextIOWrapper(f, encoding='utf-8-sig'))
				fields = reader.fieldnames
				stream = io.TextIOWrapper(o, encoding='utf-8', newline='')
				w = csv.DictWriter(stream, fields, lineterminator='\n')
				w.writeheader()
				for row in reader:
					if row['trip_id'] not in parentrows:
						w.writerow(row)
				for pair in hrrows.values():
					w.writerows(pair)
				stream.flush()
				stream.detach()
			proof.update(
				od_trips=api_trips,
				od_journeys=journeys,
				od_rejected=rejected,
				od_connections=len(api_trips),
				od_paths=len(journeys),
				mtr_timing_policy='Exact boarding/alighting pair API total, matched by line and complete station path. No subtraction from another journey. No distance fallback.',
			)
			dest.writestr(
				'mtr_api_provenance.json', json.dumps(proof, ensure_ascii=False)
			)
	tmp.replace(path)
	# Verify every compiled connection independently against its own OD evidence.
	with zipfile.ZipFile(path) as z:
		first = {}
		checked = 0
		for r in csv.DictReader(
			io.TextIOWrapper(z.open('stop_times.txt'), encoding='utf-8-sig')
		):
			tid = r['trip_id']
			if tid not in api_trips:
				continue
			if r['stop_sequence'] == '1':
				first[tid] = r
			else:
				a = first.pop(tid)
				v = journeys[api_trips[tid]['journey']]
				assert (
					r['stop_sequence'] == '2'
					and a['pickup_type'] == '0'
					and r['pickup_type'] == '1'
				)
				assert (
					seconds(r['arrival_time']) - seconds(a['departure_time'])
					== v['api_total_seconds']
				)
				assert (a['stop_id'], r['stop_id']) == (
					v['stop_ids'][0],
					v['stop_ids'][-1],
				)
				checked += 1
		assert not first and checked == len(api_trips)
	report_path = root / 'data/mtr_api/merge_report.json'
	report = json.loads(report_path.read_text())
	report.update(
		od_connections=len(api_trips),
		od_paths=len(journeys),
		od_unmatched_paths=len(rejected),
		mtr_timing_policy=proof['mtr_timing_policy'],
	)
	report_path.write_text(json.dumps(report, indent=2))
	print(
		'MTR exact OD connections:',
		len(api_trips),
		'unique paths:',
		len(journeys),
		'unsupported line/path combinations:',
		len(rejected),
		flush=True,
	)
