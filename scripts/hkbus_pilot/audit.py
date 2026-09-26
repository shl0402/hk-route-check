#!/usr/bin/env python3
"""Offline integrity, extraction and conservative route-identity audit. No GTFS writes."""

import collections, hashlib, json, re, sqlite3, unicodedata
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from scraper import ROOT, save, table_grid
from bs4 import BeautifulSoup

OUT = ROOT / 'data/wiki_pilot'


def norm(s):
	s = unicodedata.normalize('NFKC', s)
	s = re.sub(r'\(循環線\)|\(循環綫\)', '', s)
	return re.sub(r'[^\w\u3400-\u9fff]', '', s).lower()


def terminal_evidence(article, meta):
	fields = [
		f['value'] for f in article['infobox'] if f['label'] in ['起訖點', '起讫点']
	]
	raw = ' '.join(fields)
	# Exact endpoint components only; do not treat substring/fuzzy matches as proof.
	parts = [norm(x) for x in re.split(r'[↔→⇄⇆←]|往返', raw)]
	parts = [x for x in parts if x]
	for m in meta:
		start, end = norm(m['locStartNameC']), norm(m['locEndNameC'])
		if len(parts) == 2 and {start, end} == set(parts):
			return True, raw
	return False, raw


def main():
	metadata = collections.defaultdict(dict)
	for fn in ['JSON_BUS.json', 'JSON_GMB.json']:
		obj = json.loads(
			(ROOT / 'data/raw/2026-09-17/td/routes_fares' / fn).read_text(
				encoding='utf-8-sig'
			)
		)
		for f in obj['features']:
			p = f['properties']
			rid = str(p['routeId'])
			seq = str(p['routeSeq'])
			metadata[rid][seq] = {
				k: p.get(k)
				for k in [
					'routeId',
					'district',
					'routeSeq',
					'companyCode',
					'routeNameC',
					'locStartNameC',
					'locEndNameC',
					'serviceMode',
					'specialType',
					'journeyTime',
					'lastUpdateDate',
				]
			}
	compare = json.loads((OUT / 'bulk_comparison.json').read_text())
	comparison = {x['wiki_title']: x for x in compare['comparisons']}
	db = sqlite3.connect(
		'file:' + str(OUT / 'bulk.sqlite') + '?mode=ro&immutable=1', uri=True
	)
	jobs = db.execute(
		'SELECT title,pageid,revision,state FROM jobs WHERE selected=1 ORDER BY title'
	).fetchall()
	db.close()
	integrity = []
	records = []
	stats = collections.Counter()
	pageids = collections.defaultdict(list)
	table_issues = []
	cell_mismatches = []
	repeated_origins = 0
	for n, (title, pid, rev, state) in enumerate(jobs):
		if n % 300 == 0:
			print(f'Audit {n}/{len(jobs)}', flush=True)
		path = OUT / 'articles' / f'{pid}.json'
		rawpath = (
			OUT / 'raw' / (hashlib.sha256(title.encode()).hexdigest()[:20] + '.json')
		)
		try:
			a = json.loads(path.read_text())
			raw = json.loads(rawpath.read_text())['response']['parse']
			assert state == 'done' and a['pageid'] == raw['pageid'] == pid
			assert str(a['revision_id']) == str(raw['revid']) == rev
			assert (
				a['article_text'].strip()
				and raw['text']['*'].strip()
				and raw['wikitext']['*'].strip()
			)
		except Exception as e:
			integrity.append(dict(title=title, error=str(e)))
			continue
		pageids[pid].append(title)
		for candidate in a['timetable_candidates']:
			cells = a['tables'][candidate['table_index']]['rows'][
				candidate['row_index']
			]
			if candidate['cells'] != [v['text'] if v else '' for v in cells]:
				cell_mismatches.append(title)
			origins = [tuple(v['origin']) for v in cells if v]
			repeated_origins += len(origins) != len(set(origins))
		if a['issues']:
			soup = BeautifulSoup(raw['text']['*'], 'html.parser')
			for table in soup.find_all('table'):
				if table.find('table'):
					continue
				try:
					table_grid(table)
				except ValueError as e:
					heading = table.find_previous(['h2', 'h3', 'h4'])
					table_issues.append(
						dict(
							title=title,
							heading=(
								heading.get_text(' ', strip=True) if heading else ''
							),
							error=str(e),
							excerpt=table.get_text(' ', strip=True)[:350],
							raw_html=str(table),
						)
					)
		headings = [s['heading'] for s in a['sections']]
		timetable = any(re.search('服務時間|班次|時間表', s) for s in headings)
		fields = {f['label']: f['value'] for f in a['infobox']}
		flags = {
			'infobox': bool(fields),
			'tables': bool(a['tables']),
			'service_section': timetable,
			'timing_candidates': bool(a['timetable_candidates']),
			'fare_section': any('收費' in s for s in headings),
			'route_section': any(re.search('行車路線|途經街道', s) for s in headings),
			'published_running_time': '行車時間' in fields,
			'references': bool(a['external_links']),
			'table_parse_issues': bool(a['issues']),
			'has_hour_minute': any(
				t.get('layout') == 'hour_minute' for t in a['timetable_candidates']
			),
			'no_candidates_despite_service_section': timetable
			and not a['timetable_candidates'],
		}
		stats.update(k for k, v in flags.items() if v)
		c = comparison[title]
		original = c['gtfs_candidates']
		region = next(
			(
				v
				for k, v in [
					('港島專綫小巴', 'HKI'),
					('九龍專綫小巴', 'KLN'),
					('新界專綫小巴', 'NT'),
				]
				if title.startswith(k)
			),
			None,
		)
		candidates = []
		region_removed = []
		for g in original:
			meta = list(metadata.get(g['route_id'], {}).values())
			districts = {m['district'] for m in meta if m['district']}
			if region and districts and region not in districts:
				region_removed.append(g['route_id'])
				continue
			matched, terminaltext = terminal_evidence(a, meta)
			candidates.append(
				dict(gtfs=g, government_metadata=meta, exact_terminal_pair=matched)
			)
		direct_ids = set()
		for link in a['external_links']:
			u = urlparse(link)
			if u.hostname and u.hostname.endswith('.hkemobility.gov.hk'):
				direct_ids.update(parse_qs(u.query).get('route_id', []))
		evidence = []
		strong = []
		for x in candidates:
			rid = x['gtfs']['route_id']
			x['official_route_id_link'] = rid in direct_ids
			if x['official_route_id_link'] or x['exact_terminal_pair']:
				strong.append(rid)
		if not original:
			label = 'no_operator_number_candidate'
		elif not candidates:
			label = 'all_candidates_conflict_with_region'
		elif len(candidates) == 1:
			if strong:
				label = 'single_candidate_with_identity_evidence'
			else:
				label = 'single_candidate_still_needs_identity_evidence'
		else:
			label = 'multiple_variants_or_unresolved'
		records.append(
			dict(
				title=title,
				canonical_title=a['title'],
				pageid=pid,
				revision_id=rev,
				original_candidate_count=len(original),
				region=region,
				region_removed=region_removed,
				candidates=candidates,
				identity_status=label,
				evidence_supported_ids=strong,
				official_route_id_links=sorted(direct_ids),
				wiki_terminals=fields.get('起訖點'),
				flags=flags,
				parse_issues=a['issues'],
				timing_candidate_count=len(a['timetable_candidates']),
				table_count=len(a['tables']),
				gtfs_schedule_merge_ready=False,
				updated_service_text=[
					s['text'][:220]
					for s in a['sections']
					if re.search('服務時間|時間表', s['heading'])
				][:2],
			)
		)
	# Mutual uniqueness among selected pages after removing explicitly wrong minibus regions.
	reverse = collections.defaultdict(set)
	for r in records:
		for c in r['candidates']:
			reverse[c['gtfs']['route_id']].add(r['pageid'])
	for r in records:
		r['mutually_unique_identity_supported'] = (
			r['identity_status'] == 'single_candidate_with_identity_evidence'
			and len(reverse[r['candidates'][0]['gtfs']['route_id']]) == 1
		)
	summary = dict(
		selected_titles=len(jobs),
		unique_article_pages=len(pageids),
		integrity_failures=integrity,
		duplicate_page_aliases={str(k): v for k, v in pageids.items() if len(v) > 1},
		field_coverage=dict(stats),
		identity_counts=dict(
			collections.Counter(r['identity_status'] for r in records)
		),
		mutually_unique_identity_supported=sum(
			r['mutually_unique_identity_supported'] for r in records
		),
		formerly_ambiguous_now_unique_supported=sum(
			r['original_candidate_count'] > 1
			and r['mutually_unique_identity_supported']
			for r in records
		),
		formerly_ambiguous_now_single_without_support=sum(
			r['original_candidate_count'] > 1
			and r['identity_status'] == 'single_candidate_still_needs_identity_evidence'
			for r in records
		),
		rows_ready_for_schedule_merge=0,
		notes=[
			'Identity evidence is distinct from schedule correctness. All timetable candidates still carry unresolved semantics.',
			'Exact terminals use punctuation/whitespace normalization, remove only the circular-route marker, and do not use fuzzy matching.',
			'Official IDs are read only from HK eMobility route_id references. Unique evidence does not verify freshness or variants.',
			'Rows count selected titles, not all Hong Kong services. No network calls or GTFS modifications.',
		],
	)
	summary.update(
		candidate_cell_mismatches=cell_mismatches,
		candidate_rows_with_repeated_merged_cell_origins=repeated_origins,
		failed_table_count=len(table_issues),
		failed_table_sections=dict(
			collections.Counter(i['heading'] for i in table_issues)
		),
	)
	save(OUT / 'audit_table_issues.json', table_issues)
	save(OUT / 'audit_summary.json', summary)
	save(OUT / 'audit_routes.json', records)
	print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
	main()
