"""Reviewed adapters for the cached HK Railway Wiki layouts, not an unrestricted table guesser.

Retain raw row provenance. Conditional aggregate headways never become exact departures.
"""

import re, json
from copy import deepcopy
from bs4 import BeautifulSoup
from normalize import norm, seconds, days, timing_row, interval, RANGE

# Explicit reviewed table indices: ordinary terminal schedules, then additional short workings.
TABLES = {
	'AEL': ([2, 3], []),
	'ISL': ([4, 5], [6, 7, 8, 9, 10]),
	'SIL': ([6, 7], []),
	'TWL': ([8, 9], []),
	'KTL': ([6, 7], list(range(8, 17))),
	'TCL': ([2, 3], [4, 5, 6]),
	'TML': ([5, 6], list(range(7, 19))),
	'TKL': ([5, 6], [9, 10]),
	**{
		c: ([0, 1], [])
		for c in ['505', '507', '610', '614', '614P', '615', '615P', '751', '761P']
	},
	'705': ([0], []),
	'706': ([0], []),
}


def texts(row):
	return [c['text'] if c else '' for c in row]


def bg(cell):
	m = re.search(
		r'background(?:-color)?\s*:\s*(#[a-fA-F0-9]+)', cell.get('raw_html', '')
	)
	return m[1].upper() if m else None


def terminal(a, t, special=False):
	rows = t['rows']
	heading = norm(rows[0][0]['text'])
	m = re.fullmatch(r'由(.+?)開出(?:往(.+))?', heading) or re.fullmatch(
		r'(.+?)→(.+)', heading
	)
	if not m:
		raise ValueError(
			f'Unexpected reviewed heading {a["line_code"]} {t["index"]}: {heading}'
		)
	origin, dest = m[1], m[2]
	groups = []
	g = None
	qualifier = None
	for ri, row in enumerate(rows[1:], 1):
		cells = texts(row)
		label = norm(cells[0])
		d = days(cells[0]) if len(set(cells)) == 1 else None
		if d is not None:
			g = dict(
				table_index=t['index'],
				origin=origin,
				destination=dest,
				days=d,
				day_label=cells[0],
				records=[],
				special=special,
				blocked=[],
			)
			groups.append(g)
			qualifier = None
			continue
		if not g:
			continue
		q = re.search(r'以下.*?列車以(.+?)為尾站', label)
		if q:
			qualifier = dict(destination=q[1], mixed='部分' in label, color=bg(row[0]))
			continue
		rec = timing_row(cells)
		if rec:
			rec.update(row_index=ri, raw_cells=cells, destination=dest)
			if qualifier and bg(row[0]) == qualifier['color']:
				rec['destination'] = qualifier['destination']
				rec['mixed'] = qualifier['mixed']
			elif qualifier:
				qualifier = None
			if rec['kind'] == 'frequency' and rec['interval']['kind'] == 'unresolved':
				g['blocked'].append(dict(reason='Unparsed interval', row=ri, raw=cells))
				continue
			g['records'].append(rec)
		elif re.search(r'^以下|改經', label):
			raise ValueError(
				f'Unreviewed qualifier {a["line_code"]}/{t["index"]}/{ri}: {label}'
			)
	return groups


def tkl_out(a):
	t = next(t for t in a['tables'] if t['index'] == 7)
	groups = []
	g = None
	origin = None
	for ri, row in enumerate(t['rows'][3:], 3):
		cells = texts(row)
		label = norm(cells[0])
		d = days(cells[0])
		if d is not None:
			g = dict(
				table_index=7,
				origin=None,
				destination='康城',
				days=d,
				day_label=cells[0],
				records=[],
				special=False,
				replace_branch='TKS-UT',
				blocked=[],
			)
			groups.append(g)
			origin = None
			continue
		m = re.search(r'列車以(.+?)為起點', label)
		if m:
			origin = m[1]
			continue
		if not g:
			continue
		if origin not in ['北角', '調景嶺']:
			if re.search(r'\d:', label):
				raise ValueError('TKL branch missing origin qualifier')
			continue
		rec = timing_row([cells[0 if origin == '北角' else 1], cells[2]])
		if rec:
			rec.update(origin=origin, destination='康城', row_index=ri, raw_cells=cells)
			g['records'].append(rec)
	return groups


def lohas(a):
	# Only the actual departure table, never station opening hours or fare tables.
	candidates = [
		t
		for t in a['tables']
		if len(t['rows']) > 2 and texts(t['rows'][2]) == ['小時', '分鐘'] * 3
	]
	if len(candidates) != 1:
		raise ValueError('LOHAS hour/minute layout changed')
	t = candidates[0]
	result = []
	legend = ' '.join(texts(t['rows'][-1]))
	if '北角' not in legend or '#FF8800' not in t['raw_html'].upper():
		raise ValueError('Missing LOHAS destination legend')
	for ci, day in [(0, [0, 1, 2, 3, 4]), (2, [5]), (4, [6, 7])]:
		g = dict(
			table_index=t['index'],
			origin='康城',
			destination=None,
			days=day,
			day_label=texts(t['rows'][1])[ci],
			records=[],
			special=False,
			replace_branch='TKS-DT',
			blocked=[],
		)
		for ri, row in enumerate(t['rows'][3:], 3):
			if len(row) <= ci + 1 or not re.fullmatch(
				r'\d{1,2}', norm(row[ci]['text'])
			):
				continue
			h = int(norm(row[ci]['text']))
			soup = BeautifulSoup(row[ci + 1]['raw_html'], 'html.parser')
			seen = []
			for node in soup.find_all(string=True):
				for minute in re.findall(r'\d{1,2}', str(node)):
					assert int(minute) < 60
					parents = list(node.parents)
					orange = any(
						re.search(
							r'color\s*:\s*#ff8800', str(el.get('style', '')), re.I
						)
						for el in parents
						if getattr(el, 'attrs', None)
					)
					abbr = next((el for el in parents if el.name == 'abbr'), None)
					seen.append(int(minute))
					g['records'].append(
						dict(
							kind='departures',
							departure_seconds=[seconds(f'{h}:{minute}')],
							destination='北角' if orange else '調景嶺',
							row_index=ri,
							raw_cells=texts(row),
							train_run_label=abbr.get('title') if abbr else None,
						)
					)
			expected = [int(x) for x in re.findall(r'\d+', row[ci + 1]['text'])]
			if seen != expected:
				raise ValueError(
					f'LOHAS lost minute annotations at {ri}/{ci}: {seen} != {expected}'
				)
		result.append(g)
	return result


def disney(a, normalized):
	profiles = {}
	for x in normalized['hour_minute_timetables']:
		if x['issues']:
			raise ValueError('DRL unexpected minute annotation')
		if x['table_index'] not in [2, 3, 4, 5]:
			continue
		profiles[x['table_index']] = sorted(d['time_seconds'] for d in x['departures'])
	result = []
	for origin, indices in [('欣澳', [2, 4]), ('迪士尼', [3, 5])]:
		schedules = [profiles[i] for i in indices]
		lo = max(s[0] for s in schedules)
		hi = min(s[-1] for s in schedules)
		# Calendar unknown: hourly conservative gap envelope across BOTH published profiles.
		# It is a frequency model, never a fabricated listed departure or an assumed open-day calendar.
		g = dict(
			table_index=indices[0],
			origin=origin,
			destination=None,
			days=list(range(8)),
			day_label='Open/closed calendar unresolved; both profiles',
			records=[],
			special=False,
			blocked=[],
		)
		bounds = sorted({lo, hi, *range((lo // 3600 + 1) * 3600, hi, 3600)})
		for start, end in zip(bounds, bounds[1:]):
			gaps = [
				b - c
				for s in schedules
				for c, b in zip(s, s[1:])
				if c < end and b > start
			]
			if not gaps:
				raise ValueError('DRL uncovered time band')
			h = max(gaps)
			g['records'].append(
				dict(
					kind='frequency',
					start_seconds=start,
					end_seconds=end,
					interval=dict(
						kind='conditional_envelope',
						min_seconds=min(gaps),
						max_seconds=h,
						values_minutes=[min(gaps) / 60, h / 60],
						raw='Open/closed timetable gap envelope',
					),
					row_index=None,
					raw_cells=[],
					policy='Maximum scheduled gap touching this hour across both park-open and park-closed profiles. Park calendar unverified; estimated departures only.',
					supporting_tables=indices,
				)
			)
		result.append(g)
	return result


def east(a):
	t = next(t for t in a['tables'] if t['index'] == 15)
	result = []
	# Only use the branch-specific columns, NEVER assign trunk frequencies to both branches.
	for branch, col in [('LOW', 3), ('LMC', 4)]:
		for direction in ['UT', 'DT']:
			for ds, rr in [(list(range(5)), range(3, 10)), ([5, 6, 7], range(13, 22))]:
				g = dict(
					table_index=15,
					origin=None,
					destination=None,
					days=ds,
					day_label=str(ds),
					records=[],
					special=False,
					replace_branch=('LMC-' if branch == 'LMC' else '') + direction,
					blocked=[],
				)
				for ri in rr:
					row = texts(t['rows'][ri])
					match = RANGE.search(norm(row[0]))
					assert match
					raw = row[col]
					if raw == '不適用':
						continue
					# Keep every condition in provenance. Without a verified event calendar use the
					# largest published branch interval across applicable possibilities, not a guessed race day.
					unlabelled = re.sub(r'\[[^]]+\]', '', raw)
					vals = [float(v) for v in re.findall(r'\d+(?:\.\d+)?', unlabelled)]
					assert vals and all(0 < v <= 60 for v in vals)
					parsed = interval(raw)
					if parsed['kind'] == 'unresolved':
						parsed = dict(
							kind='conditional_envelope',
							min_seconds=round(min(vals) * 60),
							max_seconds=round(max(vals) * 60),
							values_minutes=vals,
							raw=raw,
						)
					g['records'].append(
						dict(
							kind='frequency',
							start_seconds=seconds(match[1]),
							end_seconds=seconds(match[2]),
							interval=parsed,
							row_index=ri,
							raw_cells=row,
							policy='Branch-section headway model, applied approximately to origin departure bands. Maximum published value where weekday/race-day conditions differ; no invented event calendar or Racecourse stop. Original terminal operating limits retained.',
						)
					)
				result.append(g)
	return result


def compile_article(a, normalized):
	code = a['line_code']
	if code == '507P':
		return terminal(a, next(t for t in a['tables'] if t['index'] == 1), True)
	if code == '751P':
		return terminal(a, next(t for t in a['tables'] if t['index'] == 5), True)
	if code == 'EAL':
		return east(a) + east_boundary_trains(a)
	if code == 'DRL':
		return disney(a, normalized)
	if a['title'] == '康城站':
		return lohas(a)
	if code not in TABLES:
		return []
	ordinary, special = TABLES[code]
	result = []
	for idx in ordinary + special:
		t = next(t for t in a['tables'] if t['index'] == idx)
		# Protect against a revision that moved table indices into combined frequency tables.
		if '綜合' in ' '.join(t['section_path']):
			raise ValueError('Combined headways are not individual route headways')
		result += terminal(a, t, idx in special)
	if code == 'TKL':
		result += tkl_out(a)
	return result


def station_key(s):
	s = norm(s).replace('(', '').replace(')', '').replace('恆', '恒')
	return {'海皇路': '屯門泳池', '大棠道': '大棠路'}.get(s, s)


def lrt_platforms(a):
	result = []
	if a['system'] != 'LRT':
		return result
	for t in a['tables']:
		if len(t['rows']) < 3:
			continue
		h = texts(t['rows'][1])
		if '車站名稱' not in h or '月台' not in h:
			continue
		ni, pi = h.index('車站名稱'), h.index('月台')
		rows = []
		for ri, row in enumerate(t['rows'][2:], 2):
			cells = texts(row)
			if len(cells) <= max(ni, pi) or not re.fullmatch(r'\d+', norm(cells[1])):
				continue
			item = dict(station=cells[ni], platform=cells[pi], row=ri)
			# Arrival/departure platforms at a terminus may span separate table rows.
			if rows and station_key(rows[-1]['station']) == station_key(
				item['station']
			):
				rows[-1]['platform_alternatives'] = list(
					dict.fromkeys([rows[-1]['platform'], item['platform']])
				)
				continue
			rows.append(item)
		if rows:
			result.append(dict(table=t['index'], rows=rows))
	return result


def east_boundary_trains(a):
	"""Bold first-train cells explicitly identify an originating service; other cells do not."""
	first = next(t for t in a['tables'] if t['index'] == 17)
	result = []
	for ri, row in enumerate(first['rows'][1:-1], 1):
		origin = norm(row[0]['text'])
		for ci, cell in enumerate(row[1:], 1):
			if not cell or not re.search(r'<(?:b|strong)>', cell['raw_html']):
				continue
			dest = norm(first['rows'][0][ci]['text']).removeprefix('往')
			if origin in ['落馬洲', '羅湖', '金鐘']:
				continue  # already covered by full-route models
			r = timing_row([cell['text']])
			assert r and r['kind'] == 'departures'
			r.update(row_index=ri, raw_cells=texts(row), destination=dest)
			result.append(
				dict(
					table_index=17,
					origin=origin,
					destination=dest,
					days=list(range(8)),
					day_label='每日首班起載列車',
					records=[r],
					special=True,
					blocked=[],
				)
			)
	last = next(t for t in a['tables'] if t['index'] == 18)
	# Last Lo Wu departure continues to Hung Hom; last Admiralty departure to Sheung Shui.
	# Identical times in nearer-destination columns are the same train, not extra services.
	for origin, dest in [('羅湖', '紅磡'), ('金鐘', '上水')]:
		ri, row = next(
			(i, r) for i, r in enumerate(last['rows']) if norm(r[0]['text']) == origin
		)
		ci = next(
			i for i, c in enumerate(last['rows'][0]) if norm(c['text']) == '往' + dest
		)
		r = timing_row([row[ci]['text']])
		assert r and r['kind'] == 'departures'
		r.update(row_index=ri, raw_cells=texts(row), destination=dest)
		result.append(
			dict(
				table_index=18,
				origin=origin,
				destination=dest,
				days=list(range(8)),
				day_label='每日尾班短途列車',
				records=[r],
				special=True,
				blocked=[],
			)
		)
	return result
