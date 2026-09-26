#!/usr/bin/env python3
"""Cached, non-AI rail wiki extraction. Default scope: every line in supplied MTR/LRT inventory."""

import argparse, csv, json, sys, time, re
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/hkbus_pilot'))
import scraper as common

common.API = 'https://hkrail.fandom.com/api.php'
NAMES = {
	'AEL': '機場快綫',
	'DRL': '迪士尼綫',
	'EAL': '東鐵綫',
	'ISL': '港島綫',
	'KTL': '觀塘綫',
	'SIL': '南港島綫',
	'TCL': '東涌綫',
	'TKL': '將軍澳綫',
	'TML': '屯馬綫',
	'TWL': '荃灣綫',
}
OUT = ROOT / 'data/rail_wiki'


def inventory():
	result = []
	for system, fn in [
		('MTR', 'mtr_lines_and_stations.csv'),
		('LRT', 'light_rail_routes_and_stops.csv'),
	]:
		with (ROOT / 'data/user_inputs/mtr' / fn).open(encoding='utf-8-sig') as f:
			rows = list(csv.DictReader(f))
		for code in sorted({r['Line Code'] for r in rows if r['Line Code']}):
			result.append(
				dict(
					system=system,
					code=code,
					title=NAMES[code] if system == 'MTR' else f'輕鐵{code}綫',
					inventory_source=fn,
					stations=sorted(
						{r['Chinese Name'] for r in rows if r['Line Code'] == code}
					),
				)
			)
	return result


def discover(client, entries):
	seen = {e['title'] for e in entries}
	comparison = []
	for title in ['港鐵', '輕鐵']:
		record = client.page(title)
		a = extract(record, dict(system='directory', code=title))
		common.save(OUT / 'directories' / (title + '.json'), a)
		for t in a['tables']:
			if not any(
				'港鐵 路綫列表' in c['text'] for row in t['rows'][:2] for c in row if c
			):
				continue
			for row in t['rows']:
				label = row[0]['text'] if row and row[0] else ''
				if not any(
					k in label
					for k in [
						'都市軌道',
						'通勤鐵路',
						'中型鐵路',
						'旅客捷運',
						'機場聯絡',
						'輕型鐵路',
						'高速鐵路',
					]
				):
					continue
				for c in row[1:]:
					if not c:
						continue
					for link in c['links']:
						href = link.get('href') or ''
						from urllib.parse import unquote

						if not href.startswith('/wiki/'):
							continue
						name = unquote(href.split('/wiki/', 1)[1]).replace('_', ' ')
						if not (
							name in NAMES.values()
							or name.startswith('輕鐵')
							or name == '廣深港高速鐵路'
						):
							continue
						comparison.append(
							dict(
								title=name,
								directory=title,
								in_supplied_inventory=name in seen,
							)
						)
						if name not in {e['title'] for e in entries}:
							m = re.fullmatch(r'輕鐵(\d+P?)綫', name)
							entries.append(
								dict(
									system=(
										'LRT'
										if name.startswith('輕鐵')
										else 'cross_boundary'
									),
									code=m[1] if m else name,
									title=name,
									inventory_source='wiki_directory',
									stations=[],
									routing_status='not_in_supplied_inventory',
								)
							)
	common.save(
		OUT / 'inventory_comparison.json',
		dict(
			entries=comparison,
			baseline_titles=sorted(seen),
			wiki_only_titles=sorted(
				{x['title'] for x in comparison if not x['in_supplied_inventory']}
			),
		),
	)
	return entries


def extract(record, entry):
	# Repair only syntactically empty or numeric-plus-stray-punctuation span attributes, retaining original raw response.
	from bs4 import BeautifulSoup
	import copy

	source_record = record
	record = copy.deepcopy(record)
	soup = BeautifulSoup(record['response']['parse']['text']['*'], 'html.parser')
	repairs = []
	for cell in soup.find_all(['td', 'th']):
		for attr in ['rowspan', 'colspan']:
			if cell.has_attr(attr):
				original = cell[attr]
				value = original.strip()
				fixed = (
					'1'
					if not value
					else (
						value.rstrip(';:') if re.fullmatch(r'\d+[;:]', value) else value
					)
				)
				if fixed != original:
					repairs.append(
						dict(attribute=attr, original=original, replacement=fixed)
					)
					cell[attr] = fixed
	record['response']['parse']['text']['*'] = str(soup)
	a = common.extract(record)
	p = record['response']['parse']
	a['span_repairs'] = repairs
	for table in a['tables']:
		# Labels immediately preceding first/last tables often use <b> rather than headings.
		node = next(
			(t for t in soup.find_all('table') if str(t) == table['raw_html']), None
		)
		before = []
		if node:
			for sibling in node.previous_siblings:
				if getattr(sibling, 'name', None) in ['table', 'h2', 'h3', 'h4']:
					break
				if hasattr(sibling, 'get_text'):
					before.append(sibling.get_text(' ', strip=True))
		if node:
			domrows = [r for r in node.find_all('tr') if r.find_parent('table') is node]
			for ri, row in enumerate(table['rows']):
				originals = {
					tuple(c['origin']): c for c in row if c and c['origin'][0] == ri
				}
				for (_, ci), cell in zip(
					sorted(originals),
					domrows[ri].find_all(['td', 'th'], recursive=False),
				):
					originals[(ri, ci)]['raw_html'] = str(cell)
		table['preceding_text'] = (
			' '.join(reversed(before)) + ' ' + (table.get('caption') or '')
		)
		if (
			'首班車' in table['preceding_text']
			and '尾班車' not in table['preceding_text']
		):
			table['service_boundary_kind'] = 'first'
		elif (
			'尾班車' in table['preceding_text']
			and '首班車' not in table['preceding_text']
		):
			table['service_boundary_kind'] = 'last'
	text = ' '.join(
		s['text'] for s in a['sections'] if s['heading'] == '服務時間及班次'
	)
	match = re.search(
		r'班次資料最後於\s*(20\d{2})年\s*(\d{1,2})月(?:\s*(\d{1,2})日)?更新', text
	)
	a['timetable_stated_update'] = (
		(
			'-'.join(
				[match[1], match[2].zfill(2)]
				+ ([match[3].zfill(2)] if match[3] else [])
			)
		)
		if match
		else None
	)
	a.update(
		system=entry['system'],
		line_code=entry['code'],
		source_url='https://hkrail.fandom.com/wiki/'
		+ quote(p['title'])
		+ '?oldid='
		+ str(p['revid']),
	)
	# Text, original wikitext/HTML, references, images, links, and all table cells are retained.
	a['useful_sections'] = [
		s
		for s in a['sections']
		if re.search(
			'路綫簡介|走綫|車站|班次|車務|服務時間|首班|尾班|短途|票務|轉乘|月台|行車時間',
			' '.join(s['path']),
		)
		and not re.search('歷史|未來發展', ' '.join(s['path']))
	]
	a['timing_export_status'] = 'EXTRACTED_NOT_AUTOMATICALLY_GTFS_SAFE'
	return a


def main():
	ap = argparse.ArgumentParser(description=__doc__)
	ap.add_argument('--offline', action='store_true')
	ap.add_argument('--refresh', action='store_true')
	ap.add_argument(
		'--line', action='append', help='MTR code or LRT number; default all'
	)
	ap.add_argument('--delay', type=float, default=1)
	args = ap.parse_args()
	client = common.Client(OUT, args.offline, args.refresh, max(1, args.delay))
	entries = discover(client, inventory())
	selected = [e for e in entries if not args.line or e['code'] in args.line]
	if args.line and set(args.line) - {e['code'] for e in entries}:
		ap.error('Unknown line code')
	common.save(
		OUT / 'inventory.json',
		dict(
			scope='Union of supplied MTR/LRT inventory and wiki current-line navigation. Wiki-only services require independent status/stop checks. Station pages and linked files are indexed, not recursively downloaded.',
			lines=entries,
		),
	)
	status_path = OUT / 'progress.json'
	status = json.loads(status_path.read_text()) if status_path.exists() else {}
	started = time.monotonic()
	failed = []
	for i, e in enumerate(selected, 1):
		try:
			r = client.page(e['title'])
			a = extract(r, e)
			common.save(OUT / 'articles' / f"{e['code']}.json", a)
			status[e['code']] = dict(
				state='complete',
				title=a['title'],
				revision=a['revision_id'],
				fetched_at=a['fetched_at'],
				tables=len(a['tables']),
				table_errors=a['issues'],
			)
			print(
				f"[{i}/{len(selected)}] {e['code']} {a['title']}: {len(a['tables'])} tables; {len(a['issues'])} table errors; source fetched {a['fetched_at']}; {time.monotonic()-started:.1f}s",
				flush=True,
			)
		except Exception as ex:
			status[e['code']] = dict(state='failed', error=str(ex))
			failed.append(e['code'])
			print(f"[{i}/{len(selected)}] FAILED {e['code']}: {ex}", flush=True)
		common.save(status_path, status)
	# A branch timetable is explicitly delegated to this linked article by the TKL page.
	if not args.line or 'TKL' in args.line:
		title = '康城站'
		try:
			record = client.page(title)
			extra = extract(record, dict(system='MTR', code='TKL'))
			common.save(OUT / 'supplements' / 'TKL_LOHAS.json', extra)
			print(
				f"Supplement: {title}; {len(extra['tables'])} tables; {len(extra['issues'])} table errors",
				flush=True,
			)
		except Exception as ex:
			failed.append(title)
			print('FAILED supplement:', ex, flush=True)
	common.save(
		OUT / 'scrape_report.json',
		dict(
			requested=len(selected),
			completed=sum(status[e['code']]['state'] == 'complete' for e in selected),
			failed=failed,
			supplement_present=(OUT / 'supplements' / 'TKL_LOHAS.json').exists(),
			lines=status,
		),
	)
	if failed:
		raise SystemExit(1)


if __name__ == '__main__':
	main()
