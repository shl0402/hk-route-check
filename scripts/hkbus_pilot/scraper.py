#!/usr/bin/env python3
"""Bounded, non-AI MediaWiki pilot. Never changes the routing GTFS."""

import argparse, collections, csv, hashlib, io, json, re, subprocess, time, unicodedata, zipfile, os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, unquote
from bs4 import BeautifulSoup

API = 'https://hkbus.fandom.com/api.php'
ROOT = Path(__file__).resolve().parents[2]
PROPS = 'text|wikitext|sections|revid|links|externallinks|categories|images|templates'
TIME = re.compile(r'(?<!\d)(?:[0-2]?\d)[:：][0-5]\d')


def clean(text):
	return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', text)).strip()


def save(path, value):
	path.parent.mkdir(parents=True, exist_ok=True)
	temp = path.with_name(path.name + '.tmp')
	temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
	os.replace(temp, path)


class Client:
	def __init__(self, out, offline=False, refresh=False, delay=1.0):
		self.out, self.offline, self.refresh, self.delay = out, offline, refresh, delay
		self.last = 0

	def page(self, title):
		key = hashlib.sha256(title.encode()).hexdigest()[:20]
		path = self.out / 'raw' / (key + '.json')
		if path.exists() and not self.refresh:
			return json.loads(path.read_text())
		if self.offline:
			raise ValueError('Not cached: ' + title)
		params = dict(
			action='parse', page=title, prop=PROPS, format='json', redirects=1, maxlag=5
		)
		url = API + '?' + urlencode(params)
		for attempt in range(3):
			time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
			# System curl uses the platform certificate store; TLS verification stays enabled.
			result = subprocess.run(
				[
					'curl',
					'--silent',
					'--show-error',
					'--location',
					'--max-time',
					'40',
					'--user-agent',
					'HKRoutingPilot/0.1 (bounded transport-data research)',
					'--write-out',
					'\n%{http_code}',
					url,
				],
				capture_output=True,
				text=True,
			)
			self.last = time.monotonic()
			body, _, status = result.stdout.rpartition('\n')
			if status in ('429', '503'):
				time.sleep(5 * (attempt + 1))
				continue
			if result.returncode or status != '200':
				raise ValueError(f'HTTP {status}: {result.stderr[:180]}')
			data = json.loads(body)
			if data.get('error', {}).get('code') == 'maxlag':
				time.sleep(5 * (attempt + 1))
				continue
			if 'parse' not in data:
				raise ValueError(str(data.get('error', data)))
			record = dict(
				requested_title=title,
				fetched_at=datetime.now(timezone.utc).isoformat(),
				request_url=url,
				response=data,
			)
			save(path, record)
			return record
		raise ValueError('Retry limit reached')


def page_title(a):
	href = a.get('href', '')
	if '/wiki/' not in href or 'redlink=1' in href:
		return None
	title = unquote(href.split('/wiki/', 1)[1].split('#')[0]).replace('_', ' ')
	return title if ':' not in title else None


def table_grid(table):
	"""Expand spans, only for this table (never consume nested table rows)."""
	occupied, rows = {}, []
	for ri, tr in enumerate(
		t for t in table.find_all('tr') if t.find_parent('table') is table
	):
		ci = 0
		for cell in tr.find_all(['td', 'th'], recursive=False):
			while (ri, ci) in occupied:
				ci += 1
			value = dict(
				text=clean(cell.get_text(' ', strip=True)),
				links=[
					dict(text=clean(a.get_text()), href=a.get('href'))
					for a in cell.find_all('a')
				],
				origin=[ri, ci],
				header=cell.name == 'th',
			)
			rs, cs = int(cell.get('rowspan', 1)), int(cell.get('colspan', 1))
			if not 1 <= rs <= 500 or not 1 <= cs <= 100:
				raise ValueError('Unsupported table span')
			for dr in range(rs):
				for dc in range(cs):
					if (ri + dr, ci + dc) in occupied:
						raise ValueError('Overlapping table cells')
					occupied[ri + dr, ci + dc] = value
			ci += cs
		cols = [c for (r, c) in occupied if r == ri]
		rows.append([occupied.get((ri, c)) for c in range(max(cols, default=-1) + 1)])
	return rows


def extract(record):
	p = record['response']['parse']
	soup = BeautifulSoup(p['text']['*'], 'html.parser')
	sections, tables, candidates, issues = [], [], [], []
	headings = []
	for el in soup.find_all(['h2', 'h3', 'h4', 'h5', 'h6', 'table']):
		if el.name.startswith('h'):
			if el.find_parent('table') or el.find_parent(class_='portable-infobox'):
				continue
			level = int(el.name[1])
			title = clean(el.get_text(' ', strip=True)).replace('[ ]', '').strip()
			headings = [(n, t) for n, t in headings if n < level] + [(level, title)]
			parts = []
			for sib in el.next_siblings:
				if getattr(sib, 'name', '') in ('h2', 'h3', 'h4', 'h5', 'h6'):
					break
				parts.append(
					sib.get_text(' ', strip=True)
					if hasattr(sib, 'get_text')
					else str(sib)
				)
			sections.append(
				dict(
					level=level,
					heading=title,
					path=[t for _, t in headings],
					text=clean(' '.join(parts)),
				)
			)
			continue
		if el.find('table'):
			continue  # Layout wrapper; complete original HTML remains available.
		context = [t for _, t in headings]
		try:
			grid = table_grid(el)
		except ValueError as e:
			issues.append(str(e))
			continue
		idx = len(tables)
		table = dict(
			index=idx,
			section_path=context,
			caption=clean(el.caption.get_text()) if el.caption else None,
			rows=grid,
			raw_html=str(el),
		)
		tables.append(table)
		if any(re.search('服務時間|班次|時間表', h) for h in context):
			for ri, row in enumerate(grid):
				texts = [c['text'] if c else '' for c in row]
				hour_minute = any(
					'小時' in c['text'] for r in grid[:3] for c in r if c
				) and any('分鐘' in c['text'] for r in grid[:3] for c in r if c)
				hour_row = hour_minute and bool(
					re.fullmatch(
						r'\d{1,2}(?:\s*[-–至]\s*\d{1,2})?', texts[0] if texts else ''
					)
				)
				if any(TIME.search(t) for t in texts) or hour_row:
					candidates.append(
						dict(
							table_index=idx,
							row_index=ri,
							section_path=context,
							cells=texts,
							layout='hour_minute' if hour_row else 'clock_time',
							status='unresolved_semantics',
							note='Source row only: direction, day, variant and headway semantics need a supported adapter.',
						)
					)
	fields = []
	for item in soup.select('.pi-data'):
		label, val = item.select_one('.pi-data-label'), item.select_one(
			'.pi-data-value'
		)
		if label and val:
			fields.append(
				dict(
					label=clean(label.get_text()),
					value=clean(val.get_text(' ', strip=True)),
				)
			)
	# Some infobox layouts use plain rows rather than portable-infobox data fields.
	for box in soup.select('.infobox'):
		for tr in box.find_all('tr'):
			cells = tr.find_all(['th', 'td'], recursive=False)
			if len(cells) == 2:
				fields.append(
					dict(
						label=clean(cells[0].get_text()),
						value=clean(cells[1].get_text(' ', strip=True)),
					)
				)
	return dict(
		title=p['title'],
		pageid=p['pageid'],
		revision_id=p.get('revid'),
		source_url='https://hkbus.fandom.com/wiki/' + p['title'].replace(' ', '_'),
		fetched_at=record['fetched_at'],
		article_text=soup.get_text('\n', strip=True),
		infobox=fields,
		sections=sections,
		tables=tables,
		timetable_candidates=candidates,
		links=p.get('links', []),
		external_links=p.get('externallinks', []),
		categories=p.get('categories', []),
		images=p.get('images', []),
		templates=p.get('templates', []),
		image_references=[
			dict(src=i.get('src'), data_src=i.get('data-src'), alt=i.get('alt'))
			for i in soup.find_all('img')
		],
		issues=issues,
		timing_export_status='NOT_VALIDATED_FOR_GTFS',
	)


def directory_entries(record):
	p = record['response']['parse']
	soup = BeautifulSoup(p['text']['*'], 'html.parser')
	entries = []
	# Find the route column by header; shuttle directories put it after district/building.
	for table in soup.find_all('table'):
		if table.find('table'):
			continue
		grid = table_grid(table)
		header = ' '.join(c['text'] for row in grid[:3] for c in row if c)
		if not (
			'路線' in header and re.search('起點|終點|出發地|目的地|總站|起訖', header)
		):
			continue
		column = next(
			(
				i
				for row in grid[:3]
				for i, c in enumerate(row)
				if c and c['text'] in ('路線', '路線編號', '路線通稱', '編號')
			),
			0,
		)
		heading = table.find_previous('h2')
		inactive = bool(heading and re.search('已停辦|已取消|過往', heading.get_text()))
		for row in grid:
			if len(row) <= column or not row[column]:
				continue
			for link in row[column]['links']:
				a = BeautifulSoup('<a></a>', 'html.parser').a
				a['href'] = link['href'] or ''
				title = page_title(a)
				if (
					title
					and (title.endswith('線') or '穿梭巴士' in title)
					and '/' not in title
				):
					entries.append(
						dict(
							title=title,
							directory=p['title'],
							status_hint='inactive' if inactive else 'unverified',
							row=[c['text'] if c else '' for c in row],
						)
					)
	return entries


def compare(entries, gtfs):
	with zipfile.ZipFile(gtfs) as z:
		routes = list(
			csv.DictReader(io.TextIOWrapper(z.open('routes.txt'), encoding='utf-8-sig'))
		)
	prefixes = {
		'九巴': 'KMB',
		'城巴': 'CTB',
		'龍運': 'LWB',
		'嶼巴': 'NLB',
		'港鐵巴士': 'LRTFeeder',
		'港島專綫小巴': 'GMB',
		'九龍專綫小巴': 'GMB',
		'新界專綫小巴': 'GMB',
		'過海隧道巴士': 'JOINT',
		'過海隧巴': 'CROSS',
		'愉景灣巴士': 'DB',
	}
	matches = []
	matched = set()
	for title in sorted({e['title'] for e in entries}):
		op = next((v for k, v in prefixes.items() if title.startswith(k)), None)
		m = re.search(r'([A-Za-z]*\d+[A-Za-z]*)(?:號)?線$', title)
		number = m.group(1).upper() if m else None
		possible = [
			r
			for r in routes
			if number
			and r['route_short_name'].upper() == number
			and (
				op in r['agency_id'].split('+')
				or (op == 'JOINT' and '+' in r['agency_id'])
				or (
					op == 'CROSS'
					and bool(set(r['agency_id'].split('+')) & {'KMB', 'CTB', 'LWB'})
				)
			)
		]
		matched.update(r['route_id'] for r in possible)
		matches.append(
			dict(
				wiki_title=title,
				operator_hint=op,
				route_number_hint=number,
				status=(
					'candidate_match_needs_endpoint_variant_check'
					if possible
					else 'no_candidate_in_gtfs'
				),
				gtfs_candidates=possible,
			)
		)
	return dict(
		scope='Provisional directory coverage; candidate matching is NOT route identity verification.',
		gtfs_route_count=len(routes),
		wiki_title_count=len(matches),
		comparisons=matches,
		gtfs_without_candidate=[r for r in routes if r['route_id'] not in matched],
	)


def run(args):
	out = Path(args.output)
	client = Client(out, args.offline, args.refresh, args.delay)
	failures = []
	directories = []
	directory_counts = {}
	entries = []
	pending = ['巴士路線']
	seen = set()
	while pending and len(seen) < args.max_directories:
		title = pending.pop(0)
		if title in seen:
			continue
		seen.add(title)
		try:
			record = client.page(title)
			p = record['response']['parse']
			directories.append(p['title'])
			found = directory_entries(record)
			entries.extend(found)
			directory_counts[title] = len(found)
			for link in p.get('links', []):
				t = link['*']
				if (
					link['ns'] == 0
					and '路線列表' in t
					and t not in seen
					and t not in pending
				):
					pending.append(t)
			print('Directory:', title, flush=True)
		except Exception as e:
			failures.append(dict(title=title, error=str(e)))
	inventory = dict(
		directories=directories,
		pending_directories=pending,
		entries=entries,
		coverage_complete=not pending and not failures,
		directory_entry_counts=directory_counts,
		note='Directory-derived candidates; historical/status filtering and full coverage not certified.',
	)
	save(out / 'inventory.json', inventory)
	save(out / 'comparison.json', compare(entries, args.gtfs))
	titles = args.page or ['九巴98D線']
	if not args.page:
		all_titles = sorted({e['title'] for e in entries})
		for prefix in [
			'城巴',
			'龍運',
			'嶼巴',
			'港島專綫小巴',
			'九龍專綫小巴',
			'新界專綫小巴',
			'居民巴士',
		]:
			t = next((t for t in all_titles if t.startswith(prefix)), None)
			if t:
				titles.append(t)
		red = next(
			(e['title'] for e in entries if e['directory'] == '公共小巴路線列表'), None
		)
		if red:
			titles.append(red)
		shuttle = next(
			(
				e['title']
				for e in entries
				if e['directory'] == '免費穿梭巴士路線列表'
				and e['status_hint'] != 'inactive'
			),
			None,
		)
		if shuttle:
			titles.append(shuttle)
	summaries = []
	for title in dict.fromkeys(titles):
		try:
			result = extract(client.page(title))
			save(out / 'articles' / f"{result['pageid']}.json", result)
			summary = {
				k: result[k] for k in ['title', 'pageid', 'revision_id', 'issues']
			}
			summary.update(
				{
					k: len(result[k])
					for k in [
						'infobox',
						'sections',
						'tables',
						'timetable_candidates',
						'external_links',
						'images',
					]
				}
			)
			summaries.append(summary)
			print('Article:', summary, flush=True)
		except Exception as e:
			failures.append(dict(title=title, error=str(e)))
	report = dict(
		articles=summaries,
		failures=failures,
		inventory_entries=len(entries),
		unique_inventory_titles=len({e['title'] for e in entries}),
		directories=len(directories),
		pending_directories=len(pending),
		gtfs_modified=False,
		limitations=[
			'Pilot extracts source evidence, not validated operational schedules.',
			'Linked PDFs/images preserved as references, not downloaded or parsed.',
			'Missing/unsupported tables must never be replaced with assumed service.',
			'Raw HTML and wikitext retained; no AI used.',
		],
	)
	save(out / 'report.json', report)
	print(json.dumps(report, ensure_ascii=False, indent=2))
	return 1 if failures else 0


if __name__ == '__main__':
	ap = argparse.ArgumentParser(description=__doc__)
	ap.add_argument('--output', default=str(ROOT / 'data/wiki_pilot'))
	ap.add_argument('--gtfs', default=str(ROOT / 'data/raw/2026-09-17/td/gtfs.zip'))
	ap.add_argument('--max-directories', type=int, default=32)
	ap.add_argument(
		'--page', action='append', help='Explicit sample article; repeat as needed'
	)
	ap.add_argument('--offline', action='store_true')
	ap.add_argument('--refresh', action='store_true')
	ap.add_argument('--delay', type=float, default=1.0)
	args = ap.parse_args()
	if args.max_directories < 1 or args.delay < 0.5:
		ap.error('Use at least one directory and delay >=0.5 seconds')
	raise SystemExit(run(args))
