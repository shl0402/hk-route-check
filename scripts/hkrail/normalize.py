#!/usr/bin/env python3
"""Normalize rail timetable evidence, preserving uncertain calendars, branches and interval ranges."""

import json, re, sys, unicodedata, os
from pathlib import Path
from collections import Counter

ROOT = Path(os.environ.get('RAIL_PROJECT_ROOT', Path(__file__).resolve().parents[2]))
OUT = Path(os.environ.get('RAIL_OUTPUT_DIR', ROOT / 'data/rail_wiki'))


def save(path, data):
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


CLOCK = r'(?:[012]?\d):[0-5]\d'
RANGE = re.compile(r'(' + CLOCK + r')\s*[-–—至]\s*(?:翌日)?(' + CLOCK + r')')


def norm(s):
	return re.sub(r'\s+', '', unicodedata.normalize('NFKC', s)).replace('毎', '每')


def seconds(s):
	h, m = map(int, s.split(':'))
	return (h + (24 if h < 3 else 0)) * 3600 + m * 60


def days(s):
	s = norm(s)
	return {
		'每日': list(range(8)),
		'星期一至四': [0, 1, 2, 3],
		'星期一至五': [0, 1, 2, 3, 4],
		'星期五': [4],
		'星期六': [5],
		'星期日及公眾假期': [6, 7],
		'星期六、日及公眾假期': [5, 6, 7],
		'星期六及公眾假期': [5, 7],
		'星期日': [6],
	}.get(s)


def interval(s):
	raw = s
	s = norm(s).replace("–", "-").replace("—", "-")
	m = re.fullmatch(r'(平均)?(\d+(?:\.\d+)?)(?:([-~/])(\d+(?:\.\d+)?))?', s)
	if not m:
		return dict(kind='unresolved', raw=raw)
	vals = [float(m[2])] + ([float(m[4])] if m[4] else [])
	if not all(0 < v <= 120 for v in vals):
		return dict(kind='unresolved', raw=raw)
	return dict(
		kind=(
			'published_average'
			if m[1]
			else (
				'range'
				if m[3] in ['-', '~']
				else 'alternating' if m[3] == '/' else 'single'
			)
		),
		values_minutes=vals,
		min_seconds=round(min(vals) * 60),
		max_seconds=round(max(vals) * 60),
		raw=raw,
	)


def timing_row(cells):
	a = norm(cells[0])
	b = cells[1] if len(cells) > 1 else cells[0]
	m = RANGE.fullmatch(a)
	if m:
		start, end = seconds(m[1]), seconds(m[2])
		if end < start:
			end += 86400
		return dict(
			kind='frequency', start_seconds=start, end_seconds=end, interval=interval(b)
		)
	if re.fullmatch(CLOCK + r'(?:[、,，/]' + CLOCK + r')*', a):
		return dict(
			kind='departures',
			departure_seconds=[seconds(v) for v in re.findall(CLOCK, a)],
		)
	return None


def normalize(a):
	groups = []
	observations = []
	station_hours = []
	stations = []
	unparsed = []
	hour_minutes = []
	for table in a['tables']:
		rows = [[c['text'] if c else '' for c in row] for row in table['rows']]
		ctx = ' '.join(table['section_path'])
		idx = table['index']
		if not rows:
			continue
		base = dict(
			table_index=idx,
			section_path=table['section_path'],
			source_url=a['source_url'],
			revision=a['revision_id'],
		)
		for hi, header in enumerate(rows[:4]):
			hour_cols = [
				i
				for i, x in enumerate(header[:-1])
				if norm(x) == '小時' and norm(header[i + 1]) == '分鐘'
			]
			if not hour_cols:
				continue
			for ci in hour_cols:
				listing = {
					**base,
					'caption': table.get('caption'),
					'calendar_or_condition': rows[hi - 1][ci] if hi else ctx,
					'departures': [],
					'issues': [],
					'gtfs_status': 'calendar_and_destination_rules_require_resolution',
				}
				for ri, row in enumerate(rows[hi + 1 :], hi + 1):
					if len(row) <= ci + 1:
						continue
					hour = norm(row[ci])
					minutes = norm(row[ci + 1])
					m = re.fullmatch(r'(\d{1,2})(?:[-–至](\d{1,2}))?', hour)
					if not m:
						continue
					if not re.fullmatch(r'\d{1,2}(?:[、,，]\d{1,2})*', minutes):
						listing['issues'].append(
							dict(row=ri, reason='annotated_or_unknown_minutes', raw=row)
						)
						continue
					lo = int(m[1])
					hi_hour = int(m[2] or m[1])
					mm = [int(v) for v in re.findall(r'\d+', minutes)]
					if not (0 <= lo <= hi_hour <= 29 and all(v < 60 for v in mm)):
						listing['issues'].append(
							dict(row=ri, reason='invalid_clock', raw=row)
						)
						continue
					for h in range(lo, hi_hour + 1):
						for minute in mm:
							listing['departures'].append(
								dict(
									time_seconds=seconds(f'{h:02}:{minute:02}'),
									row_index=ri,
									column=ci + 1,
									source_cell=table['rows'][ri][ci + 1],
								)
							)
				hour_minutes.append(listing)
			break
		heading = norm(rows[0][0])
		terminal = re.fullmatch(r'由(.+)開出', heading)
		if terminal and max(map(len, rows)) <= 2 and '服務時間' in ctx:
			current = None
			for ri, cells in enumerate(rows[1:], 1):
				unique = list(dict.fromkeys(cells))
				label = unique[0] if unique else ''
				d = days(label) if len(unique) == 1 else None
				if d is not None:
					current = {
						**base,
						'origin': terminal[1],
						'day_label': label,
						'days': d,
						'records': [],
						'notes': [],
						'issues': [],
						'special': bool(re.search('特別|綜合', ctx)),
					}
					groups.append(current)
					continue
				item = timing_row(cells)
				if item:
					if current is None:
						current = {
							**base,
							'origin': terminal[1],
							'day_label': None,
							'days': None,
							'records': [],
							'notes': [],
							'issues': ['missing_calendar'],
							'special': True,
						}
						groups.append(current)
					current['records'].append(dict(item, row_index=ri, raw_cells=cells))
					if (
						item['kind'] == 'frequency'
						and item['interval']['kind'] == 'unresolved'
					):
						current['issues'].append('unresolved_interval')
				elif current and label:
					current['notes'].append(dict(row_index=ri, text=' | '.join(unique)))
					if re.search('以下|起點|尾站|部分|改經|不設|假期除外|註', label):
						current['issues'].append('service_qualifier_requires_review')
					if re.search(CLOCK, label):
						current['issues'].append('unparsed_timing_row')
			continue
		# Preserve section-specific headways separately; these must never be mapped to every branch trip.
		hcols = {
			ci
			for row in rows[:4]
			for ci, c in enumerate(row)
			if '班次' in c and ('分鐘' in c or '計算' in c)
		}
		day_label = None
		headers = {}
		for ri, cells in enumerate(rows):
			if len(set(cells)) == 1 and days(cells[0]) is not None:
				day_label = cells[0]
			for ci, c in enumerate(cells):
				if ci in hcols and re.search('至|來往', c) and not re.search(CLOCK, c):
					headers[ci] = c
			joined = ' '.join(cells[:2])
			m = RANGE.search(norm(joined))
			if m and hcols:
				for ci in sorted(hcols):
					if ci >= len(cells):
						continue
					observations.append(
						{
							**base,
							'row_index': ri,
							'day_label': day_label,
							'time_label': joined,
							'start_seconds': seconds(m[1]),
							'end_seconds': (
								seconds(m[2])
								if seconds(m[2]) > seconds(m[1])
								else seconds(m[2]) + 86400
							),
							'section_or_direction': headers.get(ci),
							'interval': interval(cells[ci]),
							'raw_cells': cells,
							'gtfs_status': 'section_or_calendar_requires_resolution',
						}
					)
		if any('上車站' in c for c in rows[0]):
			for ri, cells in enumerate(rows[1:], 1):
				for ci, value in enumerate(cells[1:], 1):
					clocks = re.findall(CLOCK, norm(value))
					if clocks:
						station_hours.append(
							{
								**base,
								'row_index': ri,
								'station': cells[0],
								'destination': (
									rows[0][ci] if ci < len(rows[0]) else None
								),
								'kind': table.get('service_boundary_kind')
								or (
									'first'
									if '首班' in ctx
									else (
										'last'
										if '尾班' in ctx
										else 'first_or_last_requires_caption'
									)
								),
								'times': clocks,
								'raw': value,
								'warning': 'First trains at successive stations may be different services. Do not derive segment times by subtraction.',
							}
						)
		if any('車站代碼' in c or '月台' in c for row in rows[:3] for c in row):
			stations.append({**base, 'headers': rows[:3], 'rows': rows[3:]})
		if (
			re.search('服務時間|班次|首班|尾班', ctx)
			and not hcols
			and not any('上車站' in c for c in rows[0])
		):
			unparsed.append(
				{
					**base,
					'rows': rows,
					'status': 'preserved_for_layout_or_calendar_adapter',
				}
			)
	for g in groups:
		g['issues'] = sorted(set(g['issues']))
		freq = [r for r in g['records'] if r['kind'] == 'frequency']
		depart = [
			s
			for r in g['records']
			if r['kind'] == 'departures'
			for s in r['departure_seconds']
		]
		periods = sorted((r['start_seconds'], r['end_seconds']) for r in freq)
		if any(b <= a for a, b in periods) or any(
			a < prevb for (_, prevb), (a, _) in zip(periods, periods[1:])
		):
			g['issues'].append('overlapping_or_invalid_periods')
		if any(a <= s < b for a, b in periods for s in depart):
			g['issues'].append('departure_inside_frequency_period')
		if len(depart) != len(set(depart)):
			g['issues'].append('duplicate_departures')
		g['merge_candidate'] = bool(
			g['days'] and g['records'] and not g['issues'] and not g['special']
		)
	return dict(
		title=a['title'],
		line_code=a['line_code'],
		system=a['system'],
		source_url=a['source_url'],
		revision=a['revision_id'],
		fetched_at=a['fetched_at'],
		timetable_stated_update=a.get('timetable_stated_update'),
		span_repairs=a.get('span_repairs', []),
		terminal_timetables=groups,
		section_headways=observations,
		station_first_last=station_hours,
		station_platform_tables=stations,
		hour_minute_timetables=hour_minutes,
		other_timetable_tables=unparsed,
		operational_notes=[
			s
			for s in a['useful_sections']
			if re.search('馬場|短途|特別|服務時間|班次', ' '.join(s['path']))
		],
		table_errors=a['issues'],
	)


def main():
	data = [
		normalize(json.loads(p.read_text()))
		for p in sorted((OUT / 'articles').glob('*.json'))
	]
	for p in (OUT / 'supplements').glob('*.json'):
		data.append(normalize(json.loads(p.read_text())))
	save(OUT / 'normalized.json', data)
	counts = Counter()
	for a in data:
		for g in a['terminal_timetables']:
			counts['terminal_day_groups'] += 1
			counts['merge_candidate_groups'] += g['merge_candidate']
			for r in g['records']:
				counts[r['kind'] + '_rows'] += 1
				if r['kind'] == 'departures':
					counts['listed_departures'] += len(r['departure_seconds'])
				else:
					counts['interval_' + r['interval']['kind']] += 1
		counts['hour_minute_departures'] += sum(
			len(x['departures']) for x in a['hour_minute_timetables']
		)
		counts['section_headway_cells'] += len(a['section_headways'])
		counts['station_first_last_cells'] += len(a['station_first_last'])
		counts['station_platform_tables'] += len(a['station_platform_tables'])
		counts['unparsed_timetable_tables'] += len(a['other_timetable_tables'])
		counts['table_errors'] += len(a['table_errors'])
	report = dict(
		articles=len(data),
		counts=dict(counts),
		per_line=[
			dict(
				code=a['line_code'],
				title=a['title'],
				terminal_groups=len(a['terminal_timetables']),
				candidate_groups=sum(
					g['merge_candidate'] for g in a['terminal_timetables']
				),
				first_last_cells=len(a['station_first_last']),
				section_headways=len(a['section_headways']),
				issues=sorted(
					{v for g in a['terminal_timetables'] for v in g['issues']}
				),
			)
			for a in data
		],
	)
	save(OUT / 'normalization_report.json', report)
	print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
	main()
