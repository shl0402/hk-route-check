#!/usr/bin/env python3
"""Normalize cached wiki timetables and stage conservative, attributed test-GTFS updates."""

import argparse, collections, csv, hashlib, io, json, re, shutil, unicodedata, zipfile
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote
from bs4 import BeautifulSoup
from scraper import ROOT, save, table_grid
from audit import norm

WIKI = ROOT / 'data/wiki_pilot'
GEN = ROOT / 'data/generated'
WEEK = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
CLOCK = r'(?:[01]?\d|2[0-9]):[0-5]\d'


def text(s):
	return re.sub(r'\s+', '', unicodedata.normalize('NFKC', s))


def sec(s):
	h, m, *rest = map(int, s.split(':'))
	return h * 3600 + m * 60 + (rest[0] if rest else 0)


def clock(s):
	return f'{s//3600:02}:{s%3600//60:02}:{s%60:02}'


def day_rule(s):
	s = text(s).replace('(公眾假期除外)', '').replace('公眾假期除外', '')
	rules = {
		'每日': list(range(8)),
		'每天': list(range(8)),
		'星期一至五': list(range(5)),
		'星期一至六': list(range(6)),
		'星期六': [5],
		'星期日': [6],
		'星期日及公眾假期': [6, 7],
		'星期日、公眾假期': [6, 7],
		'星期六、日及公眾假期': [5, 6, 7],
		'星期六及公眾假期': [5, 7],
		'星期六及星期日': [5, 6],
		'公眾假期': [7],
	}
	return rules.get(s)


def parsed_times(s):
	s = text(s)
	if re.fullmatch(CLOCK + r'(?:[、,，]' + CLOCK + r')*', s):
		return [sec(t) for t in re.split('[、,，]', s)]
	return None


def parse_table(t):
	rows = t['rows']
	origins = []
	for row in rows[:3]:
		for i, c in enumerate(row):
			if not c:
				continue
			m = re.fullmatch(r'由(.+?)開出(?:時間)?', text(c['text']))
			if m and (m.group(1), c['origin'][1]) not in origins:
				origins.append((m.group(1), c['origin'][1]))
	if not origins:
		return [], ['unrecognized direction heading']
	hour = any(c and c['text'] == '小時' for r in rows[:3] for c in r)
	shared = len(origins) == 2 and max(len(r) for r in rows) == 3
	variant_col = any(c and c['text'] == '走線' for r in rows[:3] for c in r)
	groups = {}
	days = None
	errors = []
	for ri, row in enumerate(rows):
		distinct = list(dict.fromkeys(c['text'] for c in row if c and c['text']))
		if len(distinct) == 1 and day_rule(distinct[0]) is not None:
			days = day_rule(distinct[0])
			continue
		joined = ' '.join(distinct)
		if re.search('不設服務|暫停服務|附註', joined):
			days = None
			continue
		if days is None:
			continue
		if not re.search(CLOCK, joined) and not (
			hour and row and re.match(r'^\d', text(row[0]['text']))
		):
			if joined and not re.search('以下班次|回復正常|特快走線', joined):
				# Unknown explanatory notes are retained and make active block unmergeable.
				for g in groups.values():
					if g['days'] == days:
						g['errors'].append('unparsed note: ' + joined[:100])
			continue
		for oi, (origin, col) in enumerate(origins):
			tc = (1 if variant_col else col) if not shared else oi
			hc = 2 if shared else tc + 1
			variant = row[0]['text'] if variant_col and row[0] else ''
			key = (origin, tuple(days), variant)
			g = groups.setdefault(
				key,
				dict(
					origin=origin,
					days=days,
					variant=variant,
					table_index=t['index'],
					layout='hour_minute' if hour else 'time_interval',
					periods=[],
					departures=[],
					errors=[],
					source_rows=[],
				),
			)
			if tc >= len(row) or not row[tc]:
				continue
			s = text(row[tc]['text'])
			h = text(row[hc]['text']) if hc < len(row) and row[hc] else ''
			if not s or re.fullmatch(r'[—–-]+', s):
				continue
			g['source_rows'].append(ri)
			if hour:
				hm = re.fullmatch(r'(\d{1,2})(?:[-–至](\d{1,2}))?', s)
				if not hm or not re.fullmatch(r'\d{1,2}(?:[、,，]\d{1,2})*', h):
					g['errors'].append(
						'hour/minute footnote or invalid row: ' + s + ' | ' + h
					)
					continue
				hours = range(int(hm[1]), int(hm[2] or hm[1]) + 1)
				minutes = list(map(int, re.split('[、,，]', h)))
				if any(x > 59 for x in minutes) or any(x > 29 for x in hours):
					g['errors'].append('invalid clock')
					continue
				for hh in hours:
					for mm in minutes:
						g['departures'].append(hh * 3600 + mm * 60)
			else:
				m = re.fullmatch('(' + CLOCK + r')[-–至](' + CLOCK + ')', s)
				if m:
					if not re.fullmatch(r'\d+(?:\.\d+)?', h):
						g['errors'].append('variable or footnoted interval: ' + h)
						continue
					head = round(float(h) * 60)
					if not 60 <= head <= 10800:
						g['errors'].append('implausible interval')
						continue
					start, end = sec(m[1]), sec(m[2])
					end += 86400 if end < start else 0
					if start == end:
						g['errors'].append('zero-length period')
						continue
					g['periods'].append(
						dict(start=start, end=end, headway=head, row=ri)
					)
				else:
					tt = parsed_times(s)
					if tt is None:
						g['errors'].append('unsupported time row: ' + s)
					elif h and h != s and not re.fullmatch(r'[—–-]+', h):
						g['errors'].append('departure with unexplained adjacent cell')
					else:
						g['departures'].extend(tt)
	for g in groups.values():
		# Service day rolls over once as listed, not by assuming all pre-03:00 times belong to yesterday.
		last = -1
		shift = 0
		out = []
		for v in g['departures']:
			if v + shift < last:
				if last % 86400 >= 18 * 3600 and v < 6 * 3600:
					shift += 86400
				else:
					g['errors'].append('unordered departures')
			last = v + shift
			out.append(last)
		g['departures'] = sorted(set(out))
		last = -1
		shift = 0
		for p in g['periods']:
			if p['start'] + shift < last:
				if last % 86400 >= 18 * 3600 and p['start'] < 6 * 3600:
					shift += 86400
				else:
					g['errors'].append('overlapping or unordered periods')
			p['start'] += shift
			p['end'] += shift
			last = p['end']
		if not g['periods'] and not g['departures']:
			g['errors'].append('no timings')
	return list(groups.values()), errors


def normalize_article(a):
	repaired = []
	unrepaired = []
	tables = a['tables']
	if a['issues']:
		raw = json.loads(
			(
				WIKI
				/ 'raw'
				/ (
					hashlib.sha256(a['_requested_title'].encode()).hexdigest()[:20]
					+ '.json'
				)
			).read_text()
		)['response']['parse']['text']['*']
		soup = BeautifulSoup(raw, 'html.parser')
		tables = []
		path = []
		for el in soup.find_all(['h2', 'h3', 'h4', 'h5', 'h6', 'table']):
			if el.name.startswith('h'):
				if el.find_parent('table') or el.find_parent(class_='portable-infobox'):
					continue
				level = int(el.name[1])
				path = [p for p in path if p[0] < level] + [
					(level, el.get_text(' ', strip=True).replace('[ ]', '').strip())
				]
				continue
			if el.find('table'):
				continue
			for cell in el.select('[rowspan], [colspan]'):
				for attr in ['rowspan', 'colspan']:
					if attr not in cell.attrs:
						continue
					value = cell[attr]
					# HTML numeric attribute parsing: known stray quotes/blank only. Never invent a layout for overlaps.
					if value == '':
						cell[attr] = '1'
						repaired.append(dict(attribute=attr, before=value, after='1'))
					elif re.fullmatch(r'\d+["“”\s]+', value):
						cell[attr] = re.match(r'\d+', value)[0]
						repaired.append(
							dict(attribute=attr, before=value, after=cell[attr])
						)
			try:
				grid = table_grid(el)
			except ValueError as e:
				unrepaired.append(dict(section=[p[1] for p in path], error=str(e)))
				continue
			tables.append(
				dict(index=len(tables), rows=grid, section_path=[p[1] for p in path])
			)
	groups = []
	errors = []
	for t in tables:
		if t['section_path'] and re.search(
			'服務時間|時間表|班次', t['section_path'][0]
		):
			gg, ee = parse_table(t)
			for g in gg:
				g['section_path'] = t['section_path']
			groups.extend(gg)
			errors.extend(ee)
	return dict(
		title=a['_requested_title'],
		canonical_title=a['title'],
		pageid=a['pageid'],
		revision_id=a['revision_id'],
		source_url=a['source_url'],
		groups=groups,
		table_errors=errors,
		span_repairs=repaired,
		unrepaired=unrepaired,
	)


def read_feed(path):
	tables = {}
	fields = {}
	with zipfile.ZipFile(path) as z:
		for name in z.namelist():
			if name.endswith('.txt'):
				reader = csv.DictReader(io.StringIO(z.read(name).decode('utf-8-sig')))
				fields[name] = reader.fieldnames
				tables[name] = list(reader)
	return tables, fields


def write_feed(path, tables, fields, provenance):
	with zipfile.ZipFile(path.with_suffix('.tmp'), 'w', zipfile.ZIP_DEFLATED) as z:
		for name, rows in tables.items():
			cols = list(fields.get(name, []))
			for r in rows:
				for k in r:
					if k not in cols:
						cols.append(k)
			s = io.StringIO()
			w = csv.DictWriter(s, fieldnames=cols)
			w.writeheader()
			w.writerows(rows)
			z.writestr(name, s.getvalue())
		z.writestr('wiki_provenance.json', json.dumps(provenance, ensure_ascii=False))
	path.with_suffix('.tmp').replace(path)


def dates_for(days, start, end, holidays):
	return [
		(start + timedelta(days=i)).strftime('%Y%m%d')
		for i in range((end - start).days + 1)
		if (
			7
			if (start + timedelta(days=i)).strftime('%Y%m%d') in holidays
			else (start + timedelta(days=i)).weekday()
		)
		in days
	]


def main(args):
	target = Path(args.output)
	baseline = Path(args.base)
	assert target != baseline
	audits = json.loads((WIKI / 'audit_routes.json').read_text())
	normalized = []
	articles = {}
	for r in audits:
		a = json.loads((WIKI / 'articles' / f"{r['pageid']}.json").read_text())
		a['_requested_title'] = r['title']
		articles[r['title']] = a
		normalized.append(normalize_article(a))
	save(WIKI / 'normalized_timetables.json', normalized)
	print(
		'Normalized',
		len(normalized),
		'articles',
		sum(len(n['groups']) for n in normalized),
		'day/direction groups',
		flush=True,
	)
	tables, fields = read_feed(baseline)
	tripmap = {t['trip_id']: t for t in tables['trips.txt']}
	st = collections.defaultdict(list)
	for r in tables['stop_times.txt']:
		st[r['trip_id']].append(r)
	byroute = collections.defaultdict(list)
	for t in tables['trips.txt']:
		byroute[t['route_id']].append(t)
	cal = {r['service_id']: r for r in tables['calendar.txt']}
	exceptions = collections.defaultdict(dict)
	for r in tables['calendar_dates.txt']:
		exceptions[r['service_id']][r['date']] = r['exception_type']
	# Public holiday dates from the original feed's Sunday+public-holiday service definition, not a guessed date list.
	holiday_service = next(
		(
			sid
			for sid, c in cal.items()
			if [c.get(d) for d in WEEK] == ['0'] * 6 + ['1']
			and sum(v == '1' for v in exceptions[sid].values()) >= 15
		),
		None,
	)
	if not holiday_service:
		raise ValueError('No government public-holiday calendar found')
	holidays = {d for d, v in exceptions[holiday_service].items() if v == '1'}
	start = date.fromisoformat(args.start)
	end = date.fromisoformat(args.end)
	if start > end:
		raise ValueError('Start date must precede end date')
	c = cal[holiday_service]
	if not (
		c['start_date']
		<= start.strftime('%Y%m%d')
		<= end.strftime('%Y%m%d')
		<= c['end_date']
	):
		raise ValueError(
			'Requested dates outside the government holiday calendar; download a newer source or choose historical dates'
		)
	for year in range(start.year, end.year + 1):
		if sum(d.startswith(str(year)) for d in holidays) < 15:
			raise ValueError(
				f'Incomplete government public-holiday exceptions for {year}; cannot guess the missing holidays'
			)
	changes = []
	skipped = []
	tripproof = {}
	newtrips = []
	newst = []
	newfreq = []
	newcal = []
	newex = []
	covered = {}
	profiles = {}
	built_sids = set()
	auditmap = {r['title']: r for r in audits}
	routes = {r['route_id']: r for r in tables['routes.txt']}
	for n in normalized:
		audit = auditmap[n['title']]
		a = articles[n['title']]
		candidates = audit['candidates']
		# Unique candidate OR exact primary-terminal candidate, but no collapsing special-route records.
		strong = [c for c in candidates if c['exact_terminal_pair']]
		if len(candidates) == 1 and (
			audit['mutually_unique_identity_supported']
			or candidates[0]['government_metadata']
		):
			c = candidates[0]
		elif len(strong) == 1:
			c = strong[0]
		else:
			skipped.append(
				dict(title=n['title'], reason='route identity or variant unresolved')
			)
			continue
		rid = c['gtfs']['route_id']
		meta = c['government_metadata']
		terminal_text = next(
			(f['value'] for f in a['infobox'] if f['label'] == '起訖點'), ''
		)
		terminal_text = re.sub(r'\[.*?\]', '', terminal_text)
		terminal_parts = [norm(x) for x in re.split(r'[↔→⇄⇆←↺]', terminal_text)]
		primary_pair = any(
			len(terminal_parts) == 2
			and set(terminal_parts)
			== {norm(m['locStartNameC']), norm(m['locEndNameC'])}
			for m in meta
		)
		if not (primary_pair or c['exact_terminal_pair']):
			skipped.append(
				dict(
					title=n['title'],
					reason='primary endpoint pair differs; official ID alone insufficient for schedule replacement',
				)
			)
			continue
		if not meta:
			skipped.append(
				dict(title=n['title'], reason='missing government direction metadata')
			)
			continue
		if any('特別' in text(m['locStartNameC'] + m['locEndNameC']) for m in meta):
			skipped.append(
				dict(
					title=n['title'], reason='special variant requires separate mapping'
				)
			)
			continue
		if not n['groups']:
			skipped.append(
				dict(title=n['title'], reason='no recognized schedule groups')
			)
			continue
		if any(
			re.search('服務時間|班次', ' '.join(x['section'])) for x in n['unrepaired']
		):
			skipped.append(dict(title=n['title'], reason='unrepaired service table'))
			continue
		source_service = ' '.join(
			s['text']
			for s in a['sections']
			if re.search('服務時間|時間表', s['heading'])
		)
		dm = re.search(r'班次資料最後於(\d{4})年(\d{1,2})月更新', source_service)
		source_month = f'{dm[1]}-{int(dm[2]):02}' if dm else None
		government_month = max(m.get('lastUpdateDate', '')[:7] for m in meta)
		if source_month and source_month < government_month:
			skipped.append(
				dict(
					title=n['title'],
					reason='wiki stated month older than government metadata',
					wiki_month=source_month,
					government_month=government_month,
				)
			)
			continue
		# Known variant annotations in a main table cannot be attached to the main route blindly.
		groups = [
			g for g in n['groups'] if len(g['section_path']) == 1 and not g['variant']
		]
		if n['title'] == '九巴98D線':
			groups = [
				g
				for g in n['groups']
				if len(g['section_path']) == 1 and g['variant'] in ('', '①')
			]
		# Reject footnoted route modifications alongside main timetable even if a clock-only table exists.
		if re.search('所有.*(?:改停|取消.*站|繞經)|跑馬地馬場', source_service):
			skipped.append(
				dict(
					title=n['title'],
					reason='main schedule has unresolved route modification notes',
				)
			)
			continue
		for seq in sorted({str(m['routeSeq']) for m in meta}):
			mm = next(m for m in meta if str(m['routeSeq']) == seq)
			origin = mm['locStartNameC'] if seq == '1' else mm['locEndNameC']
			match = [g for g in groups if norm(g['origin']) == norm(origin)]
			if not match:
				continue
			old = [t for t in byroute[rid] if t['trip_id'].split('-')[1:2] == [seq]]
			if not old:
				continue
			patterns = {tuple(r['stop_id'] for r in st[t['trip_id']]) for t in old}
			if len(patterns) != 1:
				skipped.append(
					dict(
						title=n['title'],
						route_id=rid,
						seq=seq,
						reason='multiple stop patterns within route/direction',
					)
				)
				continue
			# Resolve each service category independently; do not combine conflicting duplicate tables.
			for category in range(8):
				options = [g for g in match if category in g['days']]
				if not options:
					continue
				valid = [g for g in options if not g['errors']]
				hours = [g for g in valid if g['layout'] == 'hour_minute']
				preferred = hours or valid
				if len(preferred) != 1:
					continue
				g = preferred[0]
				if (
					not source_month
				):  # Undated wiki is not demonstrated newer: only adopt if existing identity is strong.
					if not (
						c['exact_terminal_pair']
						or audit['mutually_unique_identity_supported']
					):
						continue
				dates = dates_for([category], start, end, holidays)
				if not dates or any((rid, seq, d) in covered for d in dates):
					continue
				# Where source explicitly lists departures, use them. Frequency windows remain non-exact.
				periods = g['periods']
				departures = g['departures']
				if periods and departures:
					continue  # Mixed layouts need an endpoint/overlap adapter before replacement.
				if not periods and not departures:
					continue
				# Preserve source gaps; no broad assumptions of service outside the parsed day block.
				template = min(old, key=lambda t: t['trip_id'])
				template_rows = st[template['trip_id']]
				if not template_rows or not template_rows[0]['departure_time']:
					continue
				offsets = []
				base = sec(template_rows[0]['departure_time'])
				for row in template_rows:
					rr = dict(row)
					for col in ['arrival_time', 'departure_time']:
						if rr[col]:
							rr[col] = sec(rr[col]) - base
					offsets.append(rr)
				if any(
					isinstance(rr[col], int) and rr[col] < 0
					for rr in offsets
					for col in ['arrival_time', 'departure_time']
				):
					continue
				groupkey = hashlib.sha256(
					repr((g['table_index'], g['days'], g['variant'])).encode()
				).hexdigest()[:8]
				sid = f'WIKI:S:{rid}:{seq}:{groupkey}'
				newex.extend(
					dict(service_id=sid, date=d, exception_type='1') for d in dates
				)
				source_url = (
					'https://hkbus.fandom.com/wiki/'
					+ quote(n['canonical_title'].replace(' ', '_'))
					+ '?oldid='
					+ str(n['revision_id'])
				)
				proof = dict(
					source='wiki',
					title=n['title'],
					url=source_url,
					revision=n['revision_id'],
					table_index=g['table_index'],
					source_rows=g['source_rows'],
					days=g['days'],
					origin=g['origin'],
					published_month=source_month,
					government_metadata_month=government_month,
					confidence='experimental_published_schedule',
					running_times_source='original_government_GTFS',
					base_trip_id=template['trip_id'],
					snapshot=a['fetched_at'][:10],
					warnings=[],
				)
				proof['warnings'].append(
					'Government stop sequence and running-time offsets retained; intermediate times may still be interpolated. Wiki departure data is not a live prediction.'
				)
				if not source_month:
					proof['warnings'].append(
						'Wiki timetable has no stated update month; freshness against government timings is unverified.'
					)
				slots = (
					[(p['start'], p) for p in periods]
					if periods
					else [(d, None) for d in departures]
				)
				for j, (departure, period) in enumerate(
					slots if sid not in built_sids else []
				):
					tid = f'WIKI:{rid}:{seq}:{groupkey}:{j}'
					tr = dict(template, trip_id=tid, service_id=sid)
					newtrips.append(tr)
					for rr in offsets:
						row = dict(rr, trip_id=tid)
						for col in ['arrival_time', 'departure_time']:
							if isinstance(row[col], int):
								row[col] = clock(row[col] + departure)
						newst.append(row)
					if period:
						# GTFS end is exclusive. Retain frequency service; no fabricated exact arrivals.
						newfreq.append(
							dict(
								trip_id=tid,
								start_time=clock(departure),
								end_time=clock(period['end']),
								headway_secs=str(period['headway']),
								exact_times='0',
							)
						)
					tripproof[tid] = dict(
						proof,
						timing_kind=(
							'published_headway'
							if period
							else 'published_departure_list'
						),
					)
				built_sids.add(sid)
				for d in dates:
					covered[(rid, seq, d)] = True
				changes.append(
					dict(
						title=n['title'],
						route_id=rid,
						seq=seq,
						category=category,
						dates=dates,
						new_trips=len(slots),
						timing_kind='frequency' if periods else 'departures',
						periods=periods,
						departures=departures,
						source_month=source_month,
					)
				)
	# Suppress original trips only on the replaced dates; retain source calendars outside test window.
	affected = collections.defaultdict(set)
	for rid, seq, d in covered:
		affected[(rid, seq)].add(d)
	cloned = {}
	suppressed = 0
	for tr in tables['trips.txt']:
		pieces = tr['trip_id'].split('-')
		seq = pieces[1] if len(pieces) > 1 else ''
		ds = affected.get((tr['route_id'], seq))
		if not ds:
			continue
		oldsid = tr['service_id']
		key = (oldsid, tuple(sorted(ds)))
		if key not in cloned:
			sid = 'WIKI:RETAIN:' + hashlib.sha256(repr(key).encode()).hexdigest()[:16]
			cloned[key] = sid
			if oldsid in cal:
				newcal.append(dict(cal[oldsid], service_id=sid))
			ex = dict(exceptions[oldsid])
			ex.update({d: '2' for d in ds})
			newex.extend(
				dict(service_id=sid, date=d, exception_type=et) for d, et in ex.items()
			)
		tr['service_id'] = cloned[key]
		suppressed += 1
	tables['trips.txt'] += newtrips
	tables['stop_times.txt'] += newst
	tables['frequencies.txt'] += newfreq
	tables['calendar.txt'] += newcal
	tables['calendar_dates.txt'] += newex
	attrs = tables.setdefault('attributions.txt', [])
	for tid, p in tripproof.items():
		attrs.append(
			dict(
				attribution_id='ATTR:' + tid,
				trip_id=tid,
				organization_name='Hong Kong Bus Wiki contributors (departure data); Hong Kong Transport Department (stops/running times)',
				is_producer='1',
				is_operator='0',
				is_authority='0',
				attribution_url=p['url'],
			)
		)
	provenance = dict(
		version=1,
		date_range=[args.start, args.end],
		base_sha256=hashlib.sha256(baseline.read_bytes()).hexdigest(),
		trips=tripproof,
		default_bus_source='Hong Kong Transport Department GTFS',
	)
	target.parent.mkdir(parents=True, exist_ok=True)
	write_feed(target, tables, fields, provenance)
	report = dict(
		normalized_articles=len(normalized),
		parsed_groups=sum(len(n['groups']) for n in normalized),
		clean_groups=sum(not g['errors'] for n in normalized for g in n['groups']),
		repaired_span_attributes=sum(len(n['span_repairs']) for n in normalized),
		unrepaired_tables=sum(len(n['unrepaired']) for n in normalized),
		updated_routes=len({c['route_id'] for c in changes}),
		updated_titles=len({c['title'] for c in changes}),
		new_trips=len(newtrips),
		new_frequency_rows=len(newfreq),
		new_stop_time_rows=len(newst),
		original_trips_calendar_adjusted=suppressed,
		changed_day_direction_blocks=len(changes),
		date_range=[args.start, args.end],
		changes=changes,
		skipped=skipped,
		output=str(target),
	)
	save(WIKI / 'merge_report.json', report)
	print(
		json.dumps(
			{k: v for k, v in report.items() if k not in ['changes', 'skipped']},
			ensure_ascii=False,
			indent=2,
		)
	)


if __name__ == '__main__':
	p = argparse.ArgumentParser(description=__doc__)
	p.add_argument('--base', default=str(GEN / 'hk-transit-PRE-WIKI.gtfs.zip'))
	p.add_argument('--output', default=str(GEN / 'hk-transit-WIKI.gtfs.zip'))
	p.add_argument('--start', default='2026-09-17')
	p.add_argument('--end', default='2026-10-16')
	main(p.parse_args())
