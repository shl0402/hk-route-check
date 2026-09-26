#!/usr/bin/env python3
"""Compile reviewed MTR/LRT wiki layouts into the bus-enriched experimental GTFS.
Run without network. Every accepted row retains a revision URL and transformation policy.
"""

import csv, io, json, zipfile, sys, os
from pathlib import Path
from collections import defaultdict, Counter
from datetime import date, timedelta
from copy import deepcopy
from normalize import norm, normalize, ROOT, OUT
from line_adapters import compile_article, lrt_platforms, station_key

G = Path(os.environ.get('RAIL_GENERATED_DIR', ROOT / 'data/generated'))
BASE = ROOT / 'data/generated/hk-transit-PRE-RAIL-WIKI.gtfs.zip'
TARGET = G / 'hk-transit-RAIL-WIKI.gtfs.zip'
WEEK = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']


def sec(s):
	h, m, s = map(int, s.split(':'))
	return h * 3600 + m * 60 + s


def clock(n):
	return f'{n//3600:02}:{n//60%60:02}:{n%60:02}'


def encode(rows, fields):
	o = io.StringIO()
	w = csv.DictWriter(o, fieldnames=fields, lineterminator='\n')
	w.writeheader()
	w.writerows(rows)
	return o.getvalue().encode()


def bounds(r):
	return (
		(r['start_seconds'], r['end_seconds'])
		if r['kind'] == 'frequency'
		else (min(r['departure_seconds']), max(r['departure_seconds']) + 1)
	)


def union(windows):
	result = []
	for a, b in sorted(windows):
		if b <= a:
			continue
		if result and a <= result[-1][1]:
			result[-1] = (result[-1][0], max(b, result[-1][1]))
		else:
			result.append((a, b))
	return result


def safe_rows(records):
	"""Quarantine contradictory rows and mixed full/short services; never guess a typo."""
	rejected = {}
	windows = []
	for i, r in enumerate(records):
		if r.get('mixed'):
			rejected[i] = (
				'Mixed full-length/short-turn services; use separate short-turn table and baseline full-route model'
			)
	for i, r in enumerate(records):
		if r.get('mixed'):
			continue
		for j, s in enumerate(records[i + 1 :], i + 1):
			if s.get('mixed') or (r.get('origin'), r.get('destination')) != (
				s.get('origin'),
				s.get('destination'),
			):
				continue
			if r['kind'] == 'frequency' and s['kind'] == 'frequency':
				a, b = bounds(r)
				c, d = bounds(s)
				if max(a, c) < min(b, d):
					rejected[i] = rejected[j] = 'Conflicting overlapping source bands'
			elif r['kind'] != s['kind']:
				f, d = (r, s) if r['kind'] == 'frequency' else (s, r)
				if any(
					f['start_seconds'] <= x < f['end_seconds']
					for x in d['departure_seconds']
				):
					rejected[i] = rejected[j] = (
						'Listed departure conflicts with source frequency band'
					)
	for i, why in rejected.items():
		a, b = bounds(records[i])
		if records[i].get('mixed'):
			following = next(
				(bounds(r)[0] for r in records[i + 1 :] if not r.get('mixed')), b
			)
			b = max(b, following)
		windows.append((a, b))
	return (
		[r for i, r in enumerate(records) if i not in rejected],
		union(windows),
		[
			dict(reason=v, row=records[i]['row_index'], raw=records[i]['raw_cells'])
			for i, v in rejected.items()
		],
	)


class Feed:
	def __init__(self):
		with zipfile.ZipFile(BASE) as z:
			self.payload = {n: z.read(n) for n in z.namelist()}
		names = [
			'trips.txt',
			'frequencies.txt',
			'calendar.txt',
			'calendar_dates.txt',
			'attributions.txt',
			'routes.txt',
		]
		self.tables = {
			n: list(csv.DictReader(io.StringIO(self.payload[n].decode('utf-8-sig'))))
			for n in names
		}
		self.fields = {
			n: next(csv.reader(io.StringIO(self.payload[n].decode('utf-8-sig'))))
			for n in names
		}
		self.templates = defaultdict(list)
		for row in csv.DictReader(
			io.StringIO(self.payload['stop_times.txt'].decode('utf-8-sig'))
		):
			if row['trip_id'].startswith('RAIL:'):
				self.templates[row['trip_id']].append(row)
		for rows in self.templates.values():
			rows.sort(key=lambda s: int(s['stop_sequence']))
		self.original_templates = deepcopy(self.templates)
		self.oldtrips = {r['trip_id']: r for r in self.tables['trips.txt']}
		self.oldfreq = {
			r['trip_id']: r
			for r in self.tables['frequencies.txt']
			if r['trip_id'] in self.templates
		}
		self.cal = {r['service_id']: r for r in self.tables['calendar.txt']}
		self.ex = defaultdict(dict)
		for r in self.tables['calendar_dates.txt']:
			self.ex[r['service_id']][r['date']] = r['exception_type']
		ph = next(
			sid
			for sid, c in self.cal.items()
			if [c[d] for d in WEEK] == ['0'] * 6 + ['1']
			and sum(v == '1' for v in self.ex[sid].values()) >= 15
		)
		self.holidays = {d for d, v in self.ex[ph].items() if v == '1'}
		cal = self.cal['RAIL:DAILY']
		self.start = date.fromisoformat(
			f"{cal['start_date'][:4]}-{cal['start_date'][4:6]}-{cal['start_date'][6:]}"
		)
		self.end = date.fromisoformat(
			f"{cal['end_date'][:4]}-{cal['end_date'][4:6]}-{cal['end_date'][6:]}"
		)
		self.dates = [
			self.start + timedelta(days=i)
			for i in range((self.end - self.start).days + 1)
		]
		self.stationnames = {}
		for system, file, key in [
			('MTR', 'mtr_lines_and_stations.csv', 'Station ID'),
			('LRT', 'light_rail_routes_and_stops.csv', 'Stop ID'),
		]:
			for r in csv.DictReader(
				(ROOT / 'data/user_inputs/mtr' / file).open(encoding='utf-8-sig')
			):
				self.stationnames[f'RAIL:{system}:{r[key]}'] = norm(r['Chinese Name'])
		self.proof = {}
		self.covered = defaultdict(set)
		self.newst = []
		self.changes = []
		self.skipped = []
		self.evidence = []
		self.seen = set()
		self.path_changes = []
		self.platform_tables = {}
		# Remove only consecutive identical stop IDs from the new patterns, retaining the
		# later departure as the origin offset. Existing baseline rows remain untouched.
		for tid, st in self.templates.items():
			cleaned = []
			for row in st:
				if cleaned and row['stop_id'] == cleaned[-1]['stop_id']:
					if len(cleaned) == 1:
						cleaned[-1] = row
				else:
					cleaned.append(row)
			if len(cleaned) != len(st):
				self.path_changes.append(
					dict(
						trip=tid,
						fix='Consecutive identical stop IDs collapsed',
						removed=len(st) - len(cleaned),
					)
				)
			self.templates[tid] = cleaned
		self.loop_parts = {}
		for code in ['705', '706']:
			a, b = [f'RAIL:LRT:{code}:{d}' for d in ['1', '2']]
			x, y = self.templates[a], self.templates[b]
			assert (
				x[-1]['stop_id'] == y[0]['stop_id']
				and y[-1]['stop_id'] == x[0]['stop_id']
			)
			self.templates[a] = self.join(x, y)
			self.loop_parts[a] = [a, b]
			self.path_changes.append(
				dict(trip=a, fix='Join circular route at Tin Wing', parts=[a, b])
			)

	def name(self, row):
		return self.stationnames[':'.join(row['stop_id'].split(':')[:3])]

	def join(self, a, b):
		assert a[-1]['stop_id'] == b[0]['stop_id']
		offset = sec(a[-1]['departure_time']) - sec(b[0]['departure_time'])
		return deepcopy(a) + [
			dict(
				r,
				arrival_time=clock(sec(r['arrival_time']) + offset),
				departure_time=clock(sec(r['departure_time']) + offset),
			)
			for r in b[1:]
		]

	def path(self, code, origin, dest=None):
		candidates = []
		for tid, rows in self.templates.items():
			if tid.split(':')[2] != code or tid in {
				v[1] for v in self.loop_parts.values()
			}:
				continue
			for i, r in enumerate(rows):
				if self.name(r) != norm(origin):
					continue
				if dest:
					ends = [
						j
						for j in range(i + 1, len(rows))
						if self.name(rows[j]) == norm(dest)
					]
					if not ends:
						continue
					j = ends[-1]
				else:
					if i != 0:
						continue
					j = len(rows) - 1
				candidates.append((tid, rows[i : j + 1]))
		if not candidates and code == 'TKL' and {origin, dest} <= {'北角', '康城'}:
			if origin == '北角':
				t, a = self.path(code, '北角', '調景嶺')
				_, b = self.path(code, '調景嶺', '康城')
			else:
				t, a = self.path(code, '康城', '調景嶺')
				_, b = self.path(code, '調景嶺', '北角')
			return t, self.join(a, b)
		unique = {}
		for tid, rows in candidates:
			key = tuple(
				(r['stop_id'], sec(r['arrival_time']) - sec(rows[0]['departure_time']))
				for r in rows
			)
			unique.setdefault(key, (tid, rows))
		candidates = list(unique.values())
		if len(candidates) != 1:
			raise ValueError(
				f'Path not unique {code} {origin} -> {dest}: {[c[0] for c in candidates]}'
			)
		return candidates[0]

	def add(self, a, g, r, tid, rows, sid, chosen, fallback=False):
		code = a['line_code']
		slots = (
			[None] if r['kind'] == 'frequency' else sorted(set(r['departure_seconds']))
		)
		result = []
		for departure in slots:
			signature = (
				tuple(x['stop_id'] for x in rows),
				tuple(chosen),
				tuple(bounds(r)) if departure is None else departure,
				r.get('interval', {}).get('max_seconds'),
			)
			if signature in self.seen:
				continue
			self.seen.add(signature)
			newtid = f'RAILWIKI:{code}:{len(self.proof)}'
			result.append(newtid)
			original = self.oldtrips[tid]
			trip = dict(
				original,
				trip_id=newtid,
				service_id=sid,
				trip_headsign=self.name(rows[-1]),
			)
			if code in ['507P', '751P']:
				trip['route_id'] = 'RAIL:LRT:' + code
			self.tables['trips.txt'].append(trip)
			initial = sec(rows[0]['departure_time'])
			departure = departure or 0
			offsets = []
			for i, row in enumerate(rows):
				arrival = sec(row['arrival_time']) - initial
				dep = sec(row['departure_time']) - initial
				offsets.append(
					dict(
						stop_id=row['stop_id'],
						arrival_seconds=arrival,
						departure_seconds=dep,
					)
				)
				self.newst.append(
					dict(
						row,
						trip_id=newtid,
						stop_sequence=str(i + 1),
						arrival_time=clock(arrival + departure),
						departure_time=clock(dep + departure),
						timepoint='1' if i == 0 and r['kind'] == 'departures' else '0',
					)
				)
			frequency = r['kind'] == 'frequency'
			if frequency:
				self.tables['frequencies.txt'].append(
					dict(
						trip_id=newtid,
						start_time=clock(r['start_seconds']),
						end_time=clock(r['end_seconds']),
						headway_secs=str(r['interval']['max_seconds']),
						exact_times='0',
					)
				)
			it = r.get('interval')
			kind = (it or {}).get('kind')
			policy = r.get('policy') or (
				'Retained baseline interval in unresolved wiki band.'
				if fallback
				else (
					'Conservative maximum of published interval values; approximate departures, not an exact alternating sequence.'
					if kind in ['range', 'alternating']
					else (
						'Published average interval; actual gaps vary.'
						if kind == 'published_average'
						else (
							'Published interval; approximate departures.'
							if frequency
							else 'Listed origin departure; intermediate arrival/departure times use existing estimated running times.'
						)
					)
				)
			)
			self.proof[newtid] = dict(
				original_trip=tid,
				line=code,
				system=a['system'],
				title=a['title'],
				url=a['source_url'],
				revision=a['revision_id'],
				snapshot=a['fetched_at'][:10],
				table_index=g['table_index'],
				source_row=r['row_index'],
				raw_cells=r['raw_cells'],
				days=g['days'],
				published_update=a.get('timetable_stated_update'),
				interval=it,
				timing_kind=(
					'retained_fallback'
					if fallback
					else 'published_interval' if frequency else 'listed_departure'
				),
				policy=policy,
				origin=self.name(rows[0]),
				destination=self.name(rows[-1]),
				path_offsets=offsets,
				supporting_tables=r.get('supporting_tables'),
				train_run_label=r.get('train_run_label'),
				warning='Wiki snapshot, not independently verified current operator data. '
				+ (
					'Published timetable update: '
					+ str(a['timetable_stated_update'])
					+ '. '
					if a.get('timetable_stated_update')
					else 'Wiki timetable effective date is not stated. '
				)
				+ 'Running times, interchange walks and frequency departure times remain estimates.',
			)
			names = [station_key(self.name(row)) for row in rows]
			for pt in self.platform_tables.get(code, []):
				pr = deepcopy(pt['rows'])
				pn = [station_key(x['station']) for x in pr]
				if code in ['705', '706'] and pn[-1] != pn[0]:
					pn.append(pn[0])
					pr.append(deepcopy(pr[0]))
				if pn == names:
					self.proof[newtid]['platforms'] = [
						dict(
							stop_id=row['stop_id'],
							platform=pl.get('platform_alternatives') or pl['platform'],
							source_table=pt['table'],
							source_row=pl['row'],
						)
						for row, pl in zip(rows, pr)
					]
					break
			self.tables['attributions.txt'].append(
				{
					k: v
					for k, v in dict(
						attribution_id=newtid,
						trip_id=newtid,
						organization_name=(
							'Original experimental rail model'
							if fallback
							else 'Hong Kong Railway Wiki contributors'
						),
						is_producer='1',
						is_operator='0',
						is_authority='0',
						attribution_url=a['source_url'],
					).items()
					if k in self.fields['attributions.txt']
				}
			)
		return result

	def article(self, a):
		code = a['line_code']
		n = normalize(a)
		groups = compile_article(a, n)
		self.platform_tables[code] = lrt_platforms(a)
		if code in ['507P', '751P']:
			route = dict(
				next(
					r
					for r in self.tables['routes.txt']
					if r['route_id'] == 'RAIL:LRT:505'
				),
				route_id='RAIL:LRT:' + code,
				route_short_name=code,
				route_long_name=a['title'],
				route_url=a['source_url'],
			)
			self.tables['routes.txt'].append(route)
		if code == '751P':
			self.skipped.append(
				dict(
					line=code,
					reason='Main timetable and infobox reverse terminal hours; only separately listed morning short-turn departures added',
				)
			)
		self.evidence.append(n)
		if not groups:
			reasons = {
				'506P': 'School-day calendar not supplied',
				'610P': 'School-day calendar not supplied',
				'720': 'School-day calendar not supplied',
				'751P': 'Main timetable and infobox reverse terminal operating hours; not resolved',
				'507P': 'New route has no matching original stop sequence/running-time template',
				'廣深港高速鐵路': 'Cross-boundary reserved-seat service outside urban worker routing',
				'輕鐵發現號元朗專綫': 'Special tourist/booking service, not ordinary daily transport',
				'輕鐵發現號屯門專綫': 'Special tourist/booking service, not ordinary daily transport',
			}
			self.skipped.append(
				dict(line=code, reason=reasons.get(code, 'No reviewed layout'))
			)
			return
		for g in groups:
			chosen = [
				d.strftime('%Y%m%d')
				for d in self.dates
				if (7 if d.strftime('%Y%m%d') in self.holidays else d.weekday())
				in g['days']
			]
			if not chosen or not g['records']:
				continue
			tid = None
			rows = None
			if g.get('replace_branch'):
				tid = f'RAIL:MTR:{code}:' + g['replace_branch']
				rows = self.templates[tid]
			elif not g['special']:
				tid, rows = self.path(code, g['origin'])
			accepted, windows, rejected = safe_rows(g['records'])
			g['blocked'] += rejected
			if g['blocked']:
				self.skipped.append(
					dict(
						line=code,
						table=g['table_index'],
						day=g['day_label'],
						rows=g['blocked'],
					)
				)
			sid = f'RAILWIKI:SERVICE:{len(self.changes)}:{len(self.proof)}'
			self.tables['calendar_dates.txt'] += [
				dict(service_id=sid, date=d, exception_type='1') for d in chosen
			]
			added = []
			for r in accepted:
				r = deepcopy(r)
				if code == 'EAL' and not g['special']:
					f = self.oldfreq[tid]
					r['start_seconds'] = max(r['start_seconds'], sec(f['start_time']))
					r['end_seconds'] = min(r['end_seconds'], sec(f['end_time']))
					if r['end_seconds'] <= r['start_seconds']:
						continue
					rt, rs = tid, rows
				elif code == 'DRL':
					rt, rs = tid, rows
				else:
					rt, rs = self.path(
						{'507P': '505', '751P': '751'}.get(code, code),
						r.get('origin') or g['origin'],
						r.get('destination') or g.get('destination'),
					)
				added += self.add(a, g, r, rt, rs, sid, chosen)
			if code == 'EAL' and not g['special']:
				# Any source coverage gap retains the existing model, visibly identified as such.
				lo, hi = sec(self.oldfreq[tid]['start_time']), sec(
					self.oldfreq[tid]['end_time']
				)
				covered = union(
					(max(lo, r['start_seconds']), min(hi, r['end_seconds']))
					for r in accepted
				)
				cursor = lo
				for x, y in covered:
					if x > cursor:
						windows.append((cursor, x))
					cursor = max(cursor, y)
				if cursor < hi:
					windows.append((cursor, hi))
			if tid:
				f = self.oldfreq[tid]
				lo, hi = sec(f['start_time']), sec(f['end_time'])
				for x, y in union(windows):
					x, y = max(lo, x), min(hi, y)
					if y <= x:
						continue
					r = dict(
						kind='frequency',
						start_seconds=x,
						end_seconds=y,
						interval=dict(
							kind='baseline_fallback',
							min_seconds=int(f['headway_secs']),
							max_seconds=int(f['headway_secs']),
							raw='Original experimental feed',
						),
						row_index=None,
						raw_cells=[],
					)
					added += self.add(a, g, r, tid, rows, sid, chosen, True)
			if not g['special']:
				assert tid and added
				for replaced in self.loop_parts.get(tid, [tid]):
					if self.covered[replaced].intersection(chosen):
						raise ValueError(f'Duplicate replacement {replaced} {g}')
					self.covered[replaced].update(chosen)
					self.changes.append(
						dict(
							line=code,
							original_trip=replaced,
							source_url=a['source_url'],
							table=g['table_index'],
							day=g['day_label'],
							dates=chosen,
							new_trip_ids=added,
						)
					)
			else:
				self.changes.append(
					dict(
						line=code,
						original_trip=None,
						source_url=a['source_url'],
						table=g['table_index'],
						day=g['day_label'],
						dates=chosen,
						new_trip_ids=added,
					)
				)

	def finish(self):
		for tid, replaced in self.covered.items():
			trip = self.oldtrips[tid]
			old = trip['service_id']
			sid = 'RAILWIKI:BASE:' + tid
			trip['service_id'] = sid
			self.tables['calendar.txt'].append(dict(self.cal[old], service_id=sid))
			ex = dict(self.ex[old])
			ex.update({d: '2' for d in replaced})
			self.tables['calendar_dates.txt'] += [
				dict(service_id=sid, date=d, exception_type=v) for d, v in ex.items()
			]
		for n, rows in self.tables.items():
			self.payload[n] = encode(rows, self.fields[n])
		sf = next(
			csv.reader(io.StringIO(self.payload['stop_times.txt'].decode('utf-8-sig')))
		)
		self.payload['stop_times.txt'] = (
			self.payload['stop_times.txt'].rstrip(b'\r\n')
			+ b'\n'
			+ encode(self.newst, sf).split(b'\n', 1)[1]
		)
		self.payload['rail_wiki_provenance.json'] = json.dumps(
			dict(
				version=2,
				trips=self.proof,
				policy='Reviewed line-specific adapters. Listed departures separate from explicit frequency/conditional/fallback models.',
				changes=self.changes,
				path_changes=self.path_changes,
			),
			ensure_ascii=False,
		).encode()
		self.payload['rail_wiki_evidence.json'] = json.dumps(
			dict(
				note='Source evidence only; not all observations are used by the router. Includes first/last station times, platforms, section frequencies and conditions.',
				articles=self.evidence,
			),
			ensure_ascii=False,
		).encode()
		G.mkdir(parents=True, exist_ok=True)
		OUT.mkdir(parents=True, exist_ok=True)
		tmp = TARGET.with_suffix('.tmp')
		with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
			for n, b in self.payload.items():
				z.writestr(n, b)
		tmp.replace(TARGET)
		counts = Counter(p['line'] for p in self.proof.values())
		per = []
		for code in sorted(counts):
			ps = [p for p in self.proof.values() if p['line'] == code]
			per.append(
				dict(
					line=code,
					system=ps[0]['system'],
					trips=len(ps),
					listed_departures=sum(
						p['timing_kind'] == 'listed_departure' for p in ps
					),
					frequency_periods=sum(
						p['timing_kind'] == 'published_interval' for p in ps
					),
					fallback_periods=sum(
						p['timing_kind'] == 'retained_fallback' for p in ps
					),
					conditional_periods=sum(
						(p.get('interval') or {}).get('kind') == 'conditional_envelope'
						for p in ps
					),
					patterns=len({(p['origin'], p['destination']) for p in ps}),
					platform_calls=sum(len(p.get('platforms', [])) for p in ps),
				)
			)
		report = dict(
			lines_updated=sorted(counts),
			per_line=per,
			groups_updated=len(self.changes),
			new_trips=len(self.proof),
			listed_departures=sum(
				p['timing_kind'] == 'listed_departure' for p in self.proof.values()
			),
			frequency_periods=sum(
				p['timing_kind'] == 'published_interval' for p in self.proof.values()
			),
			range_model_periods=sum(
				(p.get('interval') or {}).get('kind') == 'range'
				for p in self.proof.values()
			),
			original_patterns_adjusted=len(self.covered),
			changes=self.changes,
			skipped=self.skipped,
			path_changes=self.path_changes,
			date_range=[str(self.start), str(self.end)],
		)
		(OUT / 'merge_report.json').write_text(
			json.dumps(report, ensure_ascii=False, indent=2)
		)
		print(
			json.dumps(
				{k: v for k, v in report.items() if k not in ['changes', 'skipped']},
				indent=2,
			)
		)


def main():
	f = Feed()
	cache = ROOT / 'data/rail_wiki'
	for path in [
		*sorted((cache / 'articles').glob('*.json')),
		*sorted((cache / 'supplements').glob('*.json')),
	]:
		f.article(json.loads(path.read_text()))
	f.finish()


if __name__ == '__main__':
	main()
