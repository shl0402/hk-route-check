#!/usr/bin/env python3
"""Local two-point route checker. Run with ../.venv/bin/python server.py.
Uses OTP 2.9 GraphQL, local OSM autocomplete, and the existing experimental graph.
"""

import sys, argparse, copy, csv, io, json, math, re, subprocess, time, urllib.request, urllib.error, urllib.parse, zipfile, threading
from functools import lru_cache
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
OTP = 'http://127.0.0.1:8081/otp/gtfs/v1'
HK = ZoneInfo('Asia/Hong_Kong')
GROUPS = {
	'mtr': ['SUBWAY', 'RAIL'],
	'bus': ['BUS', 'COACH'],
	'ferry': ['FERRY'],
	'tram': ['TRAM'],
	'light_rail': ['TRAM'],
	'funicular': ['FUNICULAR'],
}
PLACES = []


def indoor_data(path):
	"""Read only allowlisted, source-backed station map packages; no user data."""
	base = ROOT / 'data/landsd/indoor'
	if path == '/api/indoor/stations':
		return json.loads((base / 'stations.json').read_text())
	match = re.fullmatch(r'/api/indoor/stations/([a-f0-9-]{36})', path)
	if not match:
		raise KeyError(path)
	index = json.loads((base / 'stations.json').read_text())
	if match.group(1) not in {s['venue_id'] for s in index['stations']}:
		raise KeyError(path)
	return json.loads((base / 'stations' / (match.group(1) + '.json')).read_text())


ROUTE_LOCK = threading.BoundedSemaphore(2)
QUERY = '''query Route($origin:PlanLabeledLocationInput!,$destination:PlanLabeledLocationInput!,$date:PlanDateTimeInput,$modes:PlanModesInput,$prefs:PlanPreferencesInput){
 planConnection(origin:$origin,destination:$destination,dateTime:$date,modes:$modes,preferences:$prefs,first:30,searchWindow:"PT1H"){
 routingErrors{code description} edges{node{duration start end waitingTime walkTime walkDistance numberOfTransfers
 legs{mode transitLeg interlineWithPreviousLeg duration distance headsign start{scheduledTime} end{scheduledTime}
 from{name lat lon stop{gtfsId platformCode}} to{name lat lon stop{gtfsId platformCode}}
 route{gtfsId shortName longName} legGeometry{points}
 steps{distance relativeDirection absoluteDirection streetName bogusName lat lon exit}
 trip{gtfsId pattern{stops{gtfsId name lat lon platformCode}}}
 }}}}}'''

MTR_SOURCE_URL = 'https://www.mtr.com.hk/en/customer/services/train_service_index.html'
TD_SOURCE_URL = 'https://static.data.gov.hk/td/pt-headway-en/gtfs.zip'
PERIOD_LABELS = [
	'Weekday morning peak',
	'Weekday evening peak',
	'Weekday non-peak',
	'Saturday',
	'Sunday / public holiday',
]
_SOURCE_CONTEXT = None


def snapshot_date(key):
	# A later `fetch` must not relabel an older active graph as newly downloaded.
	active = ROOT / 'data/generated/release-manifest.json'
	if active.exists():
		sources = json.loads(active.read_text()).get('source_manifest', {})
	else:
		p = ROOT / 'data/source_manifest.json'
		sources = json.loads(p.read_text()) if p.exists() else {}
	if key in sources:
		item = sources[key]
		return (item.get('retrieved_at') or item.get('snapshot') or 'unknown')[:10]
	return 'unknown' if active.exists() else '2026-09-17'


def source_context():
	global _SOURCE_CONTEXT
	if _SOURCE_CONTEXT is not None:
		return _SOURCE_CONTEXT
	from collections import defaultdict

	report_path = ROOT / 'data/generated/quality_report.json'
	report = json.loads(report_path.read_text()) if report_path.exists() else {}
	frequencies = defaultdict(list)
	transfers = {}
	wiki = {}
	railwiki = {}
	railapi = {}
	railapitrips = {}
	odtrips = {}
	odjourneys = {}
	feedstops = {}
	with zipfile.ZipFile(ROOT / 'data/generated/hk-transit-EXPERIMENTAL.gtfs.zip') as z:
		feedstops = {
			r['stop_id']: r
			for r in csv.DictReader(
				io.TextIOWrapper(z.open('stops.txt'), encoding='utf-8-sig')
			)
		}
		for r in csv.DictReader(
			io.StringIO(z.read('frequencies.txt').decode('utf-8-sig'))
		):
			frequencies[r['trip_id']].append(
				{
					'start': r['start_time'],
					'end': r['end_time'],
					'headwaySeconds': int(r['headway_secs']),
					'exactTimes': int(r.get('exact_times') or 0),
				}
			)
		if 'wiki_provenance.json' in z.namelist():
			wiki = json.loads(z.read('wiki_provenance.json'))
		if 'rail_wiki_provenance.json' in z.namelist():
			railwiki = json.loads(z.read('rail_wiki_provenance.json'))
		if 'mtr_api_provenance.json' in z.namelist():
			api_proof = json.loads(z.read('mtr_api_provenance.json'))
			railapi = api_proof.get('segments', {})
			railapitrips = api_proof.get('trips', {})
			odtrips = api_proof.get('od_trips', {})
			odjourneys = api_proof.get('od_journeys', {})
		surface = json.loads(z.read('surface_timing_provenance.json')) if 'surface_timing_provenance.json' in z.namelist() else {}
		indoor = json.loads(z.read('indoor_provenance.json')) if 'indoor_provenance.json' in z.namelist() else {}
		if 'transfers.txt' in z.namelist():
			for r in csv.DictReader(
				io.StringIO(z.read('transfers.txt').decode('utf-8-sig'))
			):
				if r.get('min_transfer_time'):
					transfers[(r['from_stop_id'], r['to_stop_id'])] = int(
						r['min_transfer_time']
					)
	# Index merge decisions by route ID; candidate matches remain explicitly provisional.
	decisions = {}
	decision_path = ROOT / 'data/wiki_pilot/merge_report.json'
	comparison_path = ROOT / 'data/wiki_pilot/bulk_comparison.json'
	if decision_path.exists() and comparison_path.exists():
		skipped = {
			r['title']: r
			for r in json.loads(decision_path.read_text()).get('skipped', [])
		}
		for match in json.loads(comparison_path.read_text()).get('comparisons', []):
			title = match['wiki_title']
			decision = skipped.get(title)
			if not decision:
				continue
			reason = decision['reason']
			if reason == 'wiki stated month older than government metadata':
				text = f"Wiki dated {decision['wiki_month']}; government route metadata dated {decision['government_month']}. Kept government; timetable freshness unverified."
			elif 'identity' in reason or 'endpoint' in reason:
				text = 'Wiki candidate found; route or variant match unresolved.'
			else:
				text = 'Wiki candidate found; timetable rules need review.'
			for route in match.get('gtfs_candidates', []):
				decisions.setdefault(route['route_id'], []).append(text)
	_SOURCE_CONTEXT = {
		'decisions': decisions,
		'report': report,
		'frequencies': dict(frequencies),
		'transfers': transfers,
		'wiki': wiki,
		'railwiki': railwiki,
		'railapi': railapi,
		'railapitrips': railapitrips,
		'odtrips': odtrips,
		'odjourneys': odjourneys,
		'feedstops': feedstops,
		'indoor': indoor,
		'surface': surface,
	}
	return _SOURCE_CONTEXT


@lru_cache(maxsize=1)
def mtr_station_labels():
	inventory = json.loads((ROOT / 'data/mtr_api/inventory.json').read_text())
	return {
		system: {
			str(s['ID']): s['name'] for s in inventory[system]['metadata']['stations']
		}
		for system in ('HR', 'LR')
	}


def mtr_planner_url(api_url):
	"""Human-readable companion link; keep the cached API evidence unchanged."""
	parsed = urllib.parse.urlparse(api_url or '')
	system = {
		'/share/customer/jp/api/HRRoutes/': 'HR',
		'/share/customer/jp/api/LRRoute/': 'LR',
	}.get(parsed.path)
	if parsed.hostname != 'www.mtr.com.hk' or not system:
		return api_url
	query = urllib.parse.parse_qs(parsed.query)
	origin = query.get('o', [''])[0]
	destination = query.get('d', [''])[0]
	labels = mtr_station_labels().get(system, {})
	if origin not in labels or destination not in labels:
		return api_url
	params = {
		'oLabel': labels[origin],
		'oType': system + 'Station',
		'oValue': origin,
		'dLabel': labels[destination],
		'dType': system + 'Station',
		'dValue': destination,
	}
	return 'https://www.mtr.com.hk/en/customer/jp/index.php?' + urllib.parse.urlencode(
		params, quote_via=urllib.parse.quote
	)


def raw_gtfs_id(value):
	return (value or '').partition(':')[2]


def leg_provenance(leg, previous_transit=None):
	context = source_context()
	tripid = raw_gtfs_id((leg.get('trip') or {}).get('gtfsId'))
	od = context.get('odtrips', {}).get(tripid)
	parentid = od['original_trip'] if od else tripid
	departure_clock = None
	if leg.get('start', {}).get('scheduledTime'):
		dt = datetime.fromisoformat(leg['start']['scheduledTime']).astimezone(HK)
		departure_clock = dt.hour * 3600 + dt.minute * 60 + dt.second
	if od and departure_clock is not None:
		parentid = next(
			(
				f['parent']
				for f in od.get('frequency_parents', [])
				if any(
					f['start'] <= t < f['end']
					for t in (departure_clock, departure_clock + 86400)
				)
			),
			parentid,
		)
	rw = context.get('railwiki', {}).get('trips', {}).get(parentid)
	pattern = (
		context['report']
		.get('patterns', {})
		.get(rw['original_trip'] if rw else parentid, {})
	)
	frequency = pattern.get('frequency', {})
	rows = context['frequencies'].get(tripid, [])
	if od and od.get('frequency_parents') and departure_clock is not None:

		def sec(value):
			h, m, s = map(int, value.split(':'))
			return h * 3600 + m * 60 + s

		rows = [
			r
			for r in rows
			if any(
				sec(r['start']) <= t < sec(r['end'])
				for t in (departure_clock, departure_clock + 86400)
			)
		]
	rail = ':RAIL:' in ((leg.get('route') or {}).get('gtfsId') or '')
	item = {
		'engine': 'OpenTripPlanner 2.9',
		'snapshot': snapshot_date('td'),
		'tripId': tripid,
		'sourceUrl': MTR_SOURCE_URL if rail else TD_SOURCE_URL,
		'sourceName': (
			'User MTR station data + experimental rail builder'
			if rail
			else 'Hong Kong Transport Department GTFS'
		),
		'feedIntervals': rows,
		'warnings': [],
	}
	if not leg['transitLeg']:
		indoor = context.get('indoor', {})
		active = {sid for station in indoor.get('stations', []) if station['status'] in ('activated', 'partial') for sid in station.get('bindings', {})}
		ends = [((leg.get(end) or {}).get('stop') or {}).get('gtfsId', '').split(':', 1)[-1] for end in ('from', 'to')]
		uses_indoor = any(sid in active for sid in ends)
		return {
			'engine': 'OpenTripPlanner 2.9',
			'sourceName': 'OpenStreetMap + LandsD indoor pathways' if uses_indoor else 'OpenStreetMap walking network',
			'sourceUrl': 'https://www.openstreetmap.org/copyright',
			'indoorSourceUrl': 'https://data.gov.hk/en-data/dataset/hk-landsd-openmap-3d-indoor-network' if uses_indoor else None,
			'indoorTimeModel': indoor.get('model') if uses_indoor else None,
			'snapshot': snapshot_date('osm'),
			'warnings': [
				'Walking turn times are allocated from the whole walking-leg duration by distance; they are not separately measured.'
			],
		}
	item['waitingExplanation'] = (
		'This boarding gap is calculated by OTP from the timetable/frequency model and may include boarding or transfer buffers. It is not a live arrival prediction.'
	)
	if rows:
		item['intervalExplanation'] = (
			'A headway is the interval between vehicles. It is not a measured passenger wait. Under perfectly regular service and random arrival, average wait would be half a headway; this checker does not substitute that assumption for OTP timing.'
		)
	else:
		item['intervalExplanation'] = (
			'No frequency entry for this trip template; the routing engine uses the feed stop times. Intermediate stop times may be interpolated.'
		)
	wiki = context.get('wiki', {}).get('trips', {}).get(tripid)
	if wiki:
		item.update(
			sourceName='Hong Kong Bus Wiki timetable + government stops/running times',
			sourceUrl=wiki['url'],
			snapshot=wiki['snapshot'],
			sourceType='wiki',
			wikiRevision=wiki['revision'],
			wikiPublishedMonth=wiki.get('published_month'),
			wikiTable=wiki['table_index'],
			wikiRows=wiki['source_rows'],
			timingKind=wiki['timing_kind'],
		)
		item['warnings'].extend(wiki.get('warnings', []))
		item['intervalExplanation'] = (
			'Published wiki departure times are used at the origin. Later stop times retain government running-time estimates; these are not live arrivals.'
			if wiki['timing_kind'] == 'published_departure_list'
			else 'This time-specific interval comes from the wiki timetable. OTP calculates boarding gaps from frequency service; the interval is not a live waiting-time prediction.'
		)
	else:
		item['sourceType'] = 'experimental_rail' if rail else 'government'
	if not wiki and not rail:
		rid = raw_gtfs_id((leg.get('route') or {}).get('gtfsId'))
		decisions = context.get('decisions', {}).get(rid, [])
		item['wikiStatus'] = (
			decisions[0]
			if len(set(decisions)) == 1
			else (
				'Wiki candidates need route/timetable review.'
				if decisions
				else 'No wiki override for this service.'
			)
		)
	surface = context.get('surface', {})
	pattern_id = surface.get('trips', {}).get(tripid)
	if pattern_id:
		surface_pattern = surface['patterns'][pattern_id]
		item['surfaceTiming'] = {k: v for k, v in surface_pattern.items() if k not in ('stop_ids', 'distances_m')}
		item['timingModelKind'] = surface_pattern['kind']
		item['runningTimeSource'] = 'Published timing anchors with distance-weighted intermediate estimates'
		item['intervalExplanation'] = 'Published timing points are preserved. Missing stop times are estimated in proportion to distance between those points; no live traffic adjustment.'
		item['warnings'].append('Intermediate travel times are estimates, not measured or live bus timings.')
		if surface_pattern['kind'] == 'csdi_route_distance':
			item['geometrySource'] = 'Hong Kong Transport Department / CSDI route path'
			item['geometrySourceUrl'] = surface['source_manifest'][surface_pattern['source']]['metadata_url']
		else:
			item['warnings'].append(surface_pattern['warning'])
	if rw:
		item.update(
			sourceName='Hong Kong Railway Wiki timetable + experimental rail running times',
			sourceUrl=rw['url'],
			snapshot=rw['snapshot'],
			sourceType='rail_wiki',
			wikiRevision=rw['revision'],
			wikiPublishedMonth=rw['published_update'],
			wikiTable=rw['table_index'],
			wikiRows=[rw['source_row']],
			timingKind=rw['timing_kind'],
			wikiInterval=rw['interval'],
		)
		item['warnings'].append(rw['warning'])
		item['intervalExplanation'] = rw['policy']
		item['timingModelKind'] = (rw.get('interval') or {}).get('kind')
		if rw['timing_kind'] == 'retained_fallback':
			item.update(
				sourceType='experimental_rail',
				sourceName='Original rail model: unresolved wiki band',
				sourceUrl=MTR_SOURCE_URL,
			)
		item['boardingPlatform'] = next(
			(
				p['platform']
				for p in rw.get('platforms', [])
				if p['stop_id']
				== raw_gtfs_id((leg['from'].get('stop') or {}).get('gtfsId'))
			),
			None,
		)
	if rail:
		item['wikiStatus'] = (
			(
				'Wiki band unresolved; original interval retained.'
				if rw['timing_kind'] == 'retained_fallback'
				else (
					'Wiki conditional model; event calendar unverified.'
					if item.get('timingModelKind') == 'conditional_envelope'
					else 'Wiki timetable applied.'
				)
			)
			if rw
			else 'Wiki not applied: service patterns or source freshness need review.'
		)
		item['publishedIntervals'] = [
			{'period': label, 'minutes': cell}
			for label, cell in zip(PERIOD_LABELS, frequency.get('published_cells', []))
		]
		item['line'] = frequency.get('label', '')
		item['headwayPolicy'] = (
			rw['policy'] if rw else frequency.get('policy', 'Provenance not available')
		)
		if not rw and 'early/late' in frequency.get('policy', ''):
			item['warnings'].append(
				'The current rail feed applies an early/late or maximum headway throughout the day. This overstates normal daytime intervals; no verified time-band mapping has been implemented.'
			)
		if not rw and item['line'] == 'Island Line':
			item['warnings'].append(
				'ISL: the 12-minute model interval comes from the early-morning/late-night upper limit. The published weekday non-peak range is 3.6–5 minutes; weekday peak values are 1.9 and 2.1 minutes. These are service intervals, not exact arrival predictions.'
			)
		segments = pattern.get('segments', [])
		start = ((leg['from'].get('stop') or {}).get('gtfsId') or '').split(':')
		end = ((leg['to'].get('stop') or {}).get('gtfsId') or '').split(':')
		selected = []
		active = False
		if len(start) > 2 and len(end) > 2:
			for segment in segments:
				a, b = segment['key'].rsplit(':', 1)[-1].split('>')
				if a == start[-2]:
					active = True
				if active:
					selected.append(segment)
				if active and b == end[-2]:
					break
		if rw and rw.get('path_offsets'):
			actual = rw['path_offsets']
			selected = []
			started = False
			prefix = rw['original_trip'].rsplit(':', 1)[0] + ':'
			by_edge = {
				s['key'].rsplit(':', 1)[-1]: s
				for tid, p in context['report'].get('patterns', {}).items()
				if tid.startswith(prefix)
				for s in p.get('segments', [])
			}
			for a, b in zip(actual, actual[1:]):
				if a['stop_id'] == raw_gtfs_id(
					(leg['from'].get('stop') or {}).get('gtfsId')
				):
					started = True
				if started:
					key = a['stop_id'].split(':')[2] + '>' + b['stop_id'].split(':')[2]
					if key in by_edge:
						selected.append(by_edge[key])
				if started and b['stop_id'] == raw_gtfs_id(
					(leg['to'].get('stop') or {}).get('gtfsId')
				):
					break
		trip_api = context.get('railapitrips', {}).get(tripid, {})
		trip_segments = {s['key']: s for s in trip_api.get('segments', [])}
		enriched = []
		for segment in selected:
			key = segment['key']
			parts = key.split(':')
			ends = parts[-1].split('>')
			normalized = (
				':'.join(parts[:-1]) + ':' + str(int(ends[0])) + '>' + str(int(ends[1]))
				if all(v.isdigit() for v in ends)
				else key
			)
			official = (
				trip_segments.get(normalized)
				if trip_api
				else context.get('railapi', {}).get(normalized)
			)
			enriched.append(
				dict(
					key=key,
					seconds=official['seconds'],
					kind=official['kind'],
					evidence={
						k: official[k]
						for k in (
							'source',
							'source_urls',
							'supporting_origins',
							'reason',
						)
					},
				)
				if official
				else segment
			)
		selected = enriched
		if any(
			s.get('kind') in ('OPERATOR_DERIVED_estimate', 'MTR_API_CUMULATIVE')
			for s in selected
		):
			count = sum(
				s.get('kind') in ('OPERATOR_DERIVED_estimate', 'MTR_API_CUMULATIVE')
				for s in selected
			)
			item['runningTimeSource'] = (
				f'MTR API estimates: {count}/{len(selected)} segments; remaining segments use the distance model.'
				if count < len(selected)
				else 'MTR API: operator-derived segment estimates.'
			)
			item['sourceName'] = (
				'Railway Wiki timetable' if rw else 'Rail model timetable'
			)
			item['runningTimeSourceUrl'] = next(
				s['evidence']['source_urls'][0]
				for s in selected
				if s.get('kind') in ('OPERATOR_DERIVED_estimate', 'MTR_API_CUMULATIVE')
			)
			item['warnings'] = [
				w.replace(
					'Running times, interchange walks and frequency departure times remain estimates.',
					'Interchange walks and frequency departure times remain estimates.',
				)
				for w in item['warnings']
			]
			if trip_api:
				item['runningTimeSource'] = (
					'MTR journey planner: cached cumulative timings for this train path. No distance/speed fallback.'
				)
				item['warnings'].append(
					'MTR publishes estimated journey times and may include an initial boarding allowance. That allowance cannot be separated from the API time; excluding the visible OTP first wait does not remove it.'
				)
			else:
				item['warnings'].append(
					'Running times are rounded estimates derived from same-train API paths, not measured times or live arrivals.'
				)
		item['runningTimeSegments'] = selected
		item['serviceHours'] = (
			{'kind': 'WIKI_TIMETABLE', 'reason': rw['policy']}
			if rw
			else pattern.get('service_hours', {})
		)
		if any(s.get('kind') == 'ASSUMPTION_distance_speed_model' for s in selected):
			item['warnings'].append(
				'Rail running time is an uncalibrated distance/speed estimate, not a measured train journey. The old journey-time cache is not used because it can mix waits and fallback values.'
			)
		if item['serviceHours'].get('kind') == 'ASSUMPTION':
			item['warnings'].append(
				item['serviceHours'].get('reason', 'Assumed operating hours')
			)
	if od:
		journey = context['odjourneys'][od['journey']]
		item['runningTimeSegments'] = []
		item['journeyTiming'] = {
			'seconds': journey['api_total_seconds'],
			'url': journey['url'],
			'stationIds': journey['path'],
			'line': journey['line'],
			'snapshot': journey['fetched_at'],
			'method': 'Whole cached boarding-to-alighting API journey; internal changes included.',
			'internalTransfers': journey.get('internal_transfers', 0),
		}
		item['runningTimeSource'] = (
			'MTR API: direct station-pair journey ('
			+ str(journey['api_total_seconds'] / 60)
			+ ' min).'
		)
		item['runningTimeSourceUrl'] = journey['url']
		item['warnings'] = [
			w
			for w in item['warnings']
			if not w.startswith(
				(
					'Running times are rounded',
					'Rail running time is an uncalibrated',
					'MTR publishes estimated',
				)
			)
		]
		item['warnings'].append(
			'Whole MTR journey estimate, including internal changes where shown. No extra internal transfer wait is added; the initial OTP boarding gap is separate.'
		)
	if previous_transit:
		a = raw_gtfs_id((previous_transit['to'].get('stop') or {}).get('gtfsId'))
		b = raw_gtfs_id((leg['from'].get('stop') or {}).get('gtfsId'))
		minimum = context['transfers'].get((a, b))
		if minimum is not None:
			item['minimumTransferSeconds'] = minimum
			item['transferExplanation'] = (
				'This minimum is an uncalibrated station-transfer assumption in transfers.txt. It is a constraint within the boarding gap, not another duration to add to it.'
				if a.startswith('RAIL:')
				else 'Minimum transfer constraint from the supplied GTFS.'
			)
	if item.get('journeyTiming'):
		item['journeyTiming']['plannerUrl'] = mtr_planner_url(
			item['journeyTiming']['url']
		)
	if item.get('runningTimeSourceUrl'):
		item['runningTimePlannerUrl'] = mtr_planner_url(item['runningTimeSourceUrl'])
	for segment in item.get('runningTimeSegments', []):
		evidence = segment.get('evidence', {})
		if evidence.get('source_urls'):
			evidence['planner_url'] = mtr_planner_url(evidence['source_urls'][0])
	return item


def route_sources(pref):
	return {
		'engine': 'OTP 2.9, using the local experimental graph',
		'datasetSnapshot': snapshot_date('td'),
		'documents': [
			{
				'title': 'Rail wiki: 10 MTR lines, Light Rail and selected short services',
				'url': 'https://hkrail.fandom.com/wiki/港鐵',
			},
			{
				'title': 'Wiki timetable revisions (selected routes; experimental overrides)',
				'url': 'https://hkbus.fandom.com/wiki/巴士路線',
			},
			{
				'title': 'MTR average train intervals and early/late exceptions',
				'url': MTR_SOURCE_URL,
			},
			{
				'title': 'Government GTFS download (bus, minibus, ferry, tram)',
				'url': TD_SOURCE_URL,
			},
			{
				'title': 'OpenStreetMap walking/coordinate data',
				'url': 'https://www.openstreetmap.org/copyright',
			},
		],
		'scope': 'Source details describe the local GTFS and its quality report. The running OTP graph is built separately; rebuilding the feed alone does not update it.',
		'search': {
			'windowSeconds': 3600,
			'maxCandidates': 30,
			'maxOptions': 5,
			'walkReluctance': 8 if pref == 'walking' else 2,
			'transferPreferenceCost': 1800 if pref == 'transfers' else 0,
			'explanation': 'Walking reluctance and the 1800-unit fewer-transfers penalty influence route selection. They do not add 1800 seconds to the displayed journey.',
		},
		'assumptions': [
			{
				'name': 'Rail running times',
				'value': 'MTR: direct cached boarding/alighting pair totals compiled into OTP connections. Light Rail: API-derived segments where available, otherwise its separately labelled distance model.',
				'status': 'Fallback only; accepted MTR API cumulative-time estimates replace covered segments. Per-leg details identify the source.',
			},
			{
				'name': 'Interchange minimum',
				'value': 'MTR internal changes are included in the complete API journey. Other generated rail links retain a 300-second minimum.',
				'status': 'Uncalibrated; actual station walking time is not known.',
			},
			{
				'name': 'Rail timetable conditions',
				'value': 'Published day/time bands; EAL branch and DRL open/closed-calendar models are labelled.',
				'status': 'Unknown event calendars are not treated as exact schedules. Contradictory bands retain labelled baseline intervals.',
			},
			{
				'name': 'Rail service days',
				'value': 'Daily service over the experimental build date range.',
				'status': 'Rail wiki overrides distinguish weekdays and government-listed holidays; other rail patterns retain daily service. Event services are not added.',
			},
			{
				'name': 'Government intermediate stop times',
				'value': 'About 88% of input arrival/departure fields are blank.',
				'status': 'OTP interpolates them; populated fields are not independently verified.',
			},
		],
		'firstWaitRule': 'Only the gap before first boarding is excluded from the displayed duration. Transfer gaps remain included; scheduled times and ranking are unchanged.',
	}


def graphql(query, variables=None, timeout=80):
	req = urllib.request.Request(
		OTP,
		data=json.dumps({'query': query, 'variables': variables or {}}).encode(),
		headers={'Content-Type': 'application/json'},
	)
	with urllib.request.urlopen(req, timeout=timeout) as res:
		result = json.load(res)
	if result.get('errors'):
		raise RuntimeError('; '.join(e['message'] for e in result['errors']))
	return result['data']


def timestamp(value):
	return datetime.fromisoformat(value).timestamp()


def normalize(itinerary):
	"""Remove exactly the visible gap before first boarding; retain later waits/timestamps."""
	legs = itinerary['legs']
	initial = 0
	waits = 0
	boardings = 0
	previous_transit = None
	for i, leg in enumerate(legs):
		previous = legs[i - 1]['end']['scheduledTime'] if i else itinerary['start']
		gap = max(
			0, round(timestamp(leg['start']['scheduledTime']) - timestamp(previous))
		)
		first = leg['transitLeg'] and boardings == 0
		boarding = leg['transitLeg'] and not leg.get('interlineWithPreviousLeg', False)
		if first:
			initial = gap
		elif boardings > 0:
			waits += gap
		leg['waitBeforeSeconds'] = gap
		leg['initialWaitExcluded'] = first
		if leg['mode'] == 'TRAM':
			light = raw_gtfs_id((leg.get('route') or {}).get('gtfsId')).startswith(
				'RAIL:LRT:'
			)
			leg['transportGroup'] = 'light_rail' if light else 'tram'
			leg['transportLabel'] = 'Light Rail' if light else 'Tram'
		if boarding:
			boardings += 1
		# OTP 2.9 stopCalls fails on frequency trips. Use pattern stops instead;
		# keep actual leg endpoint times, and don't invent intermediate timestamps.
		pattern = ((leg.get('trip') or {}).get('pattern') or {}).get('stops', [])
		startid = (leg['from'].get('stop') or {}).get('gtfsId')
		endid = (leg['to'].get('stop') or {}).get('gtfsId')
		a = next((n for n, s in enumerate(pattern) if s['gtfsId'] == startid), None)
		b = next(
			(
				n
				for n, s in enumerate(pattern)
				if a is not None and n > a and s['gtfsId'] == endid
			),
			None,
		)
		leg['stopCalls'] = []
		if a is not None and b is not None:
			for n, stop in enumerate(pattern[a : b + 1]):
				timing = (
					{'departure': leg['start']['scheduledTime']}
					if n == 0
					else {'arrival': leg['end']['scheduledTime']} if n == b - a else {}
				)
				leg['stopCalls'].append(
					{'stopLocation': stop, 'schedule': {'time': timing}}
				)
		context = source_context()
		rawtid = raw_gtfs_id((leg.get('trip') or {}).get('gtfsId'))
		od = context.get('odtrips', {}).get(rawtid)
		if (
			leg['transitLeg']
			and raw_gtfs_id((leg.get('route') or {}).get('gtfsId')).startswith(
				'RAIL:MTR:'
			)
			and not od
		):
			raise ValueError(
				'MTR graph is out of date: rebuild with direct station-pair timings.'
			)
		if od:
			journey = context['odjourneys'][od['journey']]
			if abs(leg['duration'] - journey['api_total_seconds']) > 1:
				raise ValueError(
					'MTR graph and direct-pair timing evidence disagree; rebuild required.'
				)
			leg['internalTransfers'] = journey.get('internal_transfers', 0)
			leg['journeyInstructions'] = journey.get('instructions', [])
			if leg['internalTransfers']:
				leg['route'] = dict(leg['route'], shortName=journey['line'])
				leg['headsign'] = 'Whole MTR journey · change trains as shown below'
			leg['apiPath'] = []
			leg['stopCalls'] = []
			starttime = datetime.fromisoformat(leg['start']['scheduledTime'])
			for index, (sid, offset) in enumerate(zip(journey['stop_ids'], journey['seconds'])):
				endpoint = leg['from'] if index == 0 else leg['to'] if index == len(journey['stop_ids']) - 1 else None
				physical = raw_gtfs_id((endpoint.get('stop') or {}).get('gtfsId')) if endpoint else None
				stop = context['feedstops'].get(physical) or context['feedstops'][sid]
				lat = float(stop['stop_lat'])
				lon = float(stop['stop_lon'])
				leg['apiPath'].append([lat, lon])
				leg['stopCalls'].append(
					{
						'stopLocation': {
							'gtfsId': sid,
							'name': stop['stop_name'],
							'lat': lat,
							'lon': lon,
						},
						'schedule': {
							'time': {
								'arrival': (
									starttime + timedelta(seconds=offset)
								).isoformat()
							}
						},
						'timingSource': 'mtr_api',
					}
				)
		leg['provenance'] = leg_provenance(leg, previous_transit)
		if leg['transitLeg']:
			intervals = leg['provenance'].get('feedIntervals', [])
			leg['departureBasis'] = (
				'frequency'
				if any(r.get('exactTimes', 0) != 1 for r in intervals)
				else 'listed'
			)
			leg['boardingAtOrigin'] = (
				od['boarding_offset_seconds'] == 0
				if od
				else a == 0 if a is not None else None
			)
		if leg['transitLeg']:
			previous_transit = leg
		leg.pop('trip', None)
		leg['estimatedRail'] = bool(
			leg.get('route') and ':RAIL:' in leg['route']['gtfsId']
		)
	duration = max(0, round(itinerary['duration']))
	itinerary.update(
		initialWaitExcludedSeconds=initial,
		displayDurationSeconds=max(0, duration - initial),
		transferWaitSeconds=waits,
		boardings=boardings,
		transfers=max(0, boardings - 1),
	)
	itinerary['rideSeconds'] = sum(l['duration'] for l in legs if l['transitLeg'])
	return itinerary


def service_window():
	config = json.loads(
		(ROOT / 'data/generated/otp-smoke/build-config.json').read_text()
	)
	start = datetime.fromisoformat(config['transitServiceStart']).replace(tzinfo=HK)
	end = datetime.fromisoformat(config['transitServiceEnd']).replace(tzinfo=HK)
	return start, end


def validate_request(data):
	if not isinstance(data, dict):
		raise ValueError('JSON object required.')
	for name in ['origin', 'destination']:
		point = data.get(name)
		if not isinstance(point, dict):
			raise ValueError('Select both locations on the map or from suggestions.')
		for key in ['lat', 'lon']:
			if (
				not isinstance(point.get(key), (int, float))
				or isinstance(point[key], bool)
				or not math.isfinite(point[key])
			):
				raise ValueError('Invalid coordinates.')
		if not (22.08 <= point['lat'] <= 22.60 and 113.80 <= point['lon'] <= 114.50):
			raise ValueError('Select a location in Hong Kong.')
	selected = data.get('modes')
	if not isinstance(selected, list) or any(x not in GROUPS for x in selected):
		raise ValueError('Invalid transport selection.')
	if data.get('preference') not in ['fastest', 'transfers', 'walking']:
		raise ValueError('Invalid route preference.')
	try:
		dt = datetime.fromisoformat(data['departure'])
	except (ValueError, KeyError, TypeError):
		raise ValueError('Choose a valid departure date and time.')
	dt = dt.replace(tzinfo=HK) if dt.tzinfo is None else dt.astimezone(HK)
	start, end = service_window()
	if not start <= dt < end:
		raise ValueError(
			f'This test graph covers {start.date()}–{(end-timedelta(days=1)).date()}. Rebuild for other dates or choose a date in that range.'
		)
	return dt


@lru_cache(maxsize=1)
def tram_route_groups():
	# Use feed identities, not route numbers or geographic guesses. Resolve OTP's
	# actual feed-scoped IDs so filtering happens before routing/ranking.
	with zipfile.ZipFile(ROOT / 'data/generated/hk-transit-EXPERIMENTAL.gtfs.zip') as z:
		groups = {
			r['route_id']: (
				'light_rail'
				if r['agency_id'] == 'RAIL:MTR'
				and r['route_id'].startswith('RAIL:LRT:')
				else 'tram'
			)
			for r in csv.DictReader(
				io.StringIO(z.read('routes.txt').decode('utf-8-sig'))
			)
			if r['route_type'] == '0'
		}
	result = {'tram': [], 'light_rail': []}
	for r in graphql('{routes{gtfsId}}')['routes']:
		group = groups.get(raw_gtfs_id(r['gtfsId']))
		if group:
			result[group].append(r['gtfsId'])
	if not all(result.values()):
		raise RuntimeError(
			'Cannot resolve Tram / Light Rail route filters; restart with the matching feed and graph.'
		)
	return result


def separate_tram_filters(selected):
	selected = set(selected)
	if ('tram' in selected) == ('light_rail' in selected):
		return []
	excluded = 'light_rail' if 'tram' in selected else 'tram'
	return [{'exclude': [{'routes': tram_route_groups()[excluded]}]}]


def has_split_mtr_journey(itinerary):
	previous_mtr = False
	for leg in itinerary['legs']:
		if not leg['transitLeg']:
			continue
		mtr = raw_gtfs_id((leg.get('route') or {}).get('gtfsId')).startswith(
			'RAIL:MTR:'
		)
		if previous_mtr and mtr:
			return True
		previous_mtr = mtr
	return False


def plan(data, for_optimization=False):
	dt = validate_request(data)
	include_alternatives = data.get('includeAlternatives', True)
	max_results = data.get('maxResults', 6)
	if not isinstance(include_alternatives, bool):
		raise ValueError('includeAlternatives must be true or false.')
	if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 10:
		raise ValueError('maxResults must be an integer from 1 to 10.')
	if for_optimization:
		include_alternatives, max_results = False, 5
	searches = ['Selected transport']
	warnings = []
	selected = data['modes']
	pref = data['preference']
	modes = {'direct': ['WALK']}
	if selected:
		modes['transit'] = {
			'access': ['WALK'],
			'egress': ['WALK'],
			'transfer': ['WALK'],
			'transit': [
				{'mode': m}
				for m in dict.fromkeys(m for group in selected for m in GROUPS[group])
			],
		}
	else:
		modes['directOnly'] = True
	variables = {
		'origin': {
			'label': 'Start',
			'location': {
				'coordinate': {
					'latitude': data['origin']['lat'],
					'longitude': data['origin']['lon'],
				}
			},
		},
		'destination': {
			'label': 'Finish',
			'location': {
				'coordinate': {
					'latitude': data['destination']['lat'],
					'longitude': data['destination']['lon'],
				}
			},
		},
		'date': {'earliestDeparture': dt.isoformat()},
		'modes': modes,
		'prefs': {
			'street': {'walk': {'reluctance': 8 if pref == 'walking' else 2}},
			'transit': {
				'board': {'waitReluctance': 1},
				'transfer': {'cost': 1800 if pref == 'transfers' else 0},
				'timetable': {'excludeRealTimeUpdates': True},
			},
		},
	}
	filters = separate_tram_filters(selected)
	if filters:
		variables['prefs']['transit']['filters'] = filters
	started = time.monotonic()
	if set(selected) == {'mtr'}:
		variables['prefs']['transit']['transfer']['maximumTransfers'] = 0
	# Fast-mode replay needs a usable departure, not 30 alternatives over an hour.
	query = QUERY.replace('first:30,searchWindow:"PT1H"', 'first:6,searchWindow:"PT10M"') if for_optimization == 'fast' else QUERY
	with ROUTE_LOCK:
		result = graphql(query, variables)['planConnection']
		if 'mtr' in selected and len(selected) > 1:
			# Keep a complete MTR candidate even if the mixed search preferred an
			# invalid sum of shorter MTR journeys. No display-only timing correction.
			railvars = copy.deepcopy(variables)
			railvars['modes']['transit']['transit'] = [
				{'mode': m} for m in GROUPS['mtr']
			]
			railvars['prefs']['transit'].pop('filters', None)
			railvars['prefs']['transit']['transfer']['maximumTransfers'] = 0
			searches.append('MTR')
			railresult = graphql(query, railvars)['planConnection']
			result['edges'] = (result['edges'] or []) + (railresult['edges'] or [])
		if include_alternatives and max_results > 1 and len(selected) > 1:
			# OTP can suppress slower but useful modes in a combined search.
			# Run bounded, user-allowed subsets instead of merely requesting more
			# copies of the already-dominant mode. Worker replay does not do this.
			profiles = []
			if 'mtr' in selected:
				profiles.append(('Other transport', [m for m in selected if m != 'mtr']))
			if 'bus' in selected and not any(set(m) == {'bus'} for _, m in profiles):
				profiles.append(('Bus / minibus', ['bus']))
			for label, subset in profiles:
				altvars = copy.deepcopy(variables)
				altvars['modes']['transit']['transit'] = [
					{'mode': m} for m in dict.fromkeys(m for group in subset for m in GROUPS[group])
				]
				altvars['prefs']['transit']['transfer'].pop('maximumTransfers', None)
				altvars['prefs']['transit'].pop('filters', None)
				altfilters = separate_tram_filters(subset)
				if altfilters:
					altvars['prefs']['transit']['filters'] = altfilters
				searches.append(label)
				try:
					alt = graphql(query, altvars, timeout=25)['planConnection']
					result['edges'] = (result['edges'] or []) + (alt['edges'] or [])
				except Exception:
					warnings.append(label + ' search unavailable; showing other completed searches.')
	items = [
		normalize(edge['node'])
		for edge in result['edges'] or []
		if not has_split_mtr_journey(edge['node'])
	]
	# Display adjustment does NOT change the route ranking, per the user's request.
	if for_optimization:
		key = lambda it: (timestamp(it['end']), it['walkDistance'])
	elif pref == 'walking':
		key = lambda it: (it['walkDistance'], it['duration'])
	elif pref == 'transfers':
		key = lambda it: (it['transfers'], it['duration'])
	else:
		key = lambda it: (it['duration'], it['walkDistance'])
	seen = set()
	unique = []
	for it in sorted(items, key=key):
		signature = tuple(
			(
				l['mode'],
				(l.get('route') or {}).get('gtfsId'),
				round(l['from']['lat'], 4),
				round(l['from']['lon'], 4),
				round(l['to']['lat'], 4),
				round(l['to']['lon'], 4),
			)
			for l in it['legs']
		)
		if signature not in seen:
			seen.add(signature)
			unique.append(it)
	if for_optimization == 'fast' and not unique:
		return plan(data, for_optimization=True)
	sources = route_sources(pref)
	if 'search' in sources:
		sources['search'].update(maxOptions=max_results, maxCandidates=(6 if for_optimization == 'fast' else 30) * len(searches), windowSeconds=600 if for_optimization == 'fast' else 3600, profiles=searches)
		sources['search']['explanation'] += ' Allowed transport subsets add variety; all results keep the same preference and are deduplicated.' if include_alternatives else ''
	return {
		'itineraries': unique[:max_results],
		'includeAlternatives': include_alternatives,
		'maxResults': max_results,
		'searches': searches,
		'warnings': warnings,
		'errors': result['routingErrors'],
		'elapsedSeconds': round(time.monotonic() - started, 2),
		'searchedCandidates': len(items),
		'ranking': pref,
		'sources': sources,
		'note': ('Best alternatives returned by OTP in a 10-minute departure window.' if for_optimization == 'fast' else 'Best alternatives returned by OTP in a 60-minute departure window.') + ' Not a proof of global optimality.',
	}


def build_places():
	from place_search import build_places as build_index
	return build_index(ROOT, HERE / 'places.json')


def search(query):
	q = query.strip().casefold()
	if len(q) < 2:
		return []
	tokens = q.split()
	matches = [p for p in PLACES if all(t in p.get('searchCompact', p['search']) for t in tokens)]
	matches.sort(
		key=lambda p: (
			not any(part.strip().casefold() == q for part in p['name'].split('·')),
			not p['name'].casefold().startswith(q),
			p['kind'] not in ['MTR station', 'Light rail station', 'Station', 'Transit stop', 'Suburb', 'Town', 'Village'],
			len(p['name']),
		)
	)
	return [{k: v for k, v in p.items() if k not in ('search', 'searchCompact')} for p in matches[:10]]


_OPTIMIZER = None
_OPTIMIZER_LOCK = threading.Lock()


def optimizer_manager():
	global _OPTIMIZER
	with _OPTIMIZER_LOCK:
		if _OPTIMIZER is None:
			from optimizer import Manager
			_OPTIMIZER = Manager(sys.modules[__name__])
	return _OPTIMIZER


def optimization_example(count=4):
	locations = json.loads((HERE / 'multi_examples.json').read_text())[:count]
	start, end = service_window()
	day = datetime.now(HK).replace(hour=9,minute=0,second=0,microsecond=0)
	if not start <= day < end-timedelta(hours=9):
		day = start.replace(hour=9)
	jobs = []
	for i,location in enumerate(locations):
		jobs.append(dict(location,id=f'job-{i+1}',serviceMinutes=20))
	return dict(jobs=jobs,shift=dict(start=day.isoformat(),end=(day+timedelta(hours=9)).isoformat(),startLocation=None,endLocation=None),
		modes=['mtr','bus','ferry','light_rail','tram','funicular'],serviceMinutes=20,planningMode='large' if count>20 else 'fast',solverSeconds=20 if count>20 else 10,maxRounds=10,maxRuntimeSeconds=600) | {
		'break':dict(minutes=30,earliest=(day+timedelta(hours=3)).isoformat(),latest=(day+timedelta(hours=5)).isoformat())}


class Handler(BaseHTTPRequestHandler):
	def send_json(self, data, status=200):
		raw = json.dumps(data, ensure_ascii=False).encode()
		self.send_response(status)
		self.send_header('Content-Type', 'application/json; charset=utf-8')
		self.send_header('Cache-Control', 'no-store')
		self.send_header('Content-Length', str(len(raw)))
		self.end_headers()
		self.wfile.write(raw)

	def do_GET(self):
		from urllib.parse import urlparse, parse_qs

		url = urlparse(self.path)
		if url.path.startswith('/api/indoor/'):
			try:
				return self.send_json(indoor_data(url.path))
			except (KeyError, FileNotFoundError):
				return self.send_json({'error': 'Indoor dataset not found'}, 404)
		if url.path == '/api/optimization/example':
			try:
				count=int(parse_qs(url.query).get('count',['4'])[0])
				if not 1 <= count <= 100: raise ValueError('Example count must be 1–100.')
				return self.send_json(optimization_example(count))
			except ValueError as exc:
				return self.send_json({'error':str(exc)},400)
		if url.path.startswith('/api/optimizations/'):
			try:
				after=int(parse_qs(url.query).get('after',['0'])[0])
				return self.send_json(optimizer_manager().get(url.path.rsplit('/',1)[-1],after))
			except KeyError:
				return self.send_json({'error':'Unknown optimisation.'},404)
			except ValueError as exc:
				return self.send_json({'error':str(exc)},400)
		if url.path == '/api/search':
			return self.send_json(search(parse_qs(url.query).get('q', [''])[0][:150]))
		if url.path == '/api/sources':
			return self.send_json(route_sources('fastest'))
		if url.path == '/api/status':
			try:
				graphql('{__typename}', timeout=2)
				ready = True
			except Exception:
				ready = False
			return self.send_json(
				dict(
					ready=ready,
					railApiSegments=len(source_context().get('railapi', {})),
					railWikiTrips=len(
						source_context().get('railwiki', {}).get('trips', {})
					),
					wikiTrips=len(source_context().get('wiki', {}).get('trips', {})),
					places=len(PLACES),
					defaultDeparture=datetime.now(HK).strftime('%Y-%m-%dT%H:%M'),
					minDate=service_window()[0].strftime('%Y-%m-%dT%H:%M'),
					maxDate=(service_window()[1] - timedelta(minutes=1)).strftime(
						'%Y-%m-%dT%H:%M'
					),
				)
			)
		if url.path not in ['/', '/index.html', '/multi', '/multi.html']:
			return self.send_error(404)
		raw = (HERE / ('multi.html' if url.path in ['/multi','/multi.html'] else 'index.html')).read_bytes()
		self.send_response(200)
		self.send_header('Content-Type', 'text/html; charset=utf-8')
		self.send_header('Content-Length', str(len(raw)))
		self.end_headers()
		self.wfile.write(raw)

	def do_POST(self):
		if self.path.startswith('/api/optimizations/') and self.path.endswith('/cancel'):
			try:
				return self.send_json(optimizer_manager().cancel(self.path.split('/')[-2]))
			except KeyError:
				return self.send_json({'error':'Unknown optimisation.'},404)
		if self.path not in ['/api/route', '/api/optimizations']:
			return self.send_error(404)
		try:
			size = int(self.headers.get('Content-Length', '0'))
			if not 0 < size <= (524288 if self.path == '/api/optimizations' else 16384):
				raise ValueError('Invalid request size.')
			if not self.headers.get('Content-Type', '').startswith('application/json'):
				raise ValueError('JSON request required.')
			data = json.loads(self.rfile.read(size))
			if self.path == '/api/optimizations':
				try:
					return self.send_json(optimizer_manager().start(data),202)
				except RuntimeError as exc:
					return self.send_json({'error':str(exc)},409)
			self.send_json(plan(data))
		except (ValueError, TypeError, KeyError) as e:
			self.send_json({'error': str(e)}, 400)
		except Exception as e:
			self.send_json({'error': 'Routing failed: ' + str(e)}, 502)

	def log_message(self, fmt, *args):
		print(fmt % args, flush=True)


def main():
	global PLACES, OTP
	p = argparse.ArgumentParser()
	p.add_argument('--port', type=int, default=8000)
	p.add_argument('--otp-port', type=int, default=8081)
	a = p.parse_args()
	OTP = f'http://127.0.0.1:{a.otp_port}/otp/gtfs/v1'
	child = None
	log = None
	try:
		try:
			graphql('{__typename}', timeout=2)
		except Exception:
			log = (HERE / 'otp.log').open('a')
			child = subprocess.Popen(
				[
					'java',
					'-Xmx4G',
					'-jar',
					str(ROOT / 'data/user_inputs/otp/otp-shaded-2.9.0.jar'),
					'--load',
					'--serve',
					'--bindAddress',
					'127.0.0.1',
					'--port',
					str(a.otp_port),
					str(ROOT / 'data/generated/otp-smoke'),
				],
				stdout=log,
				stderr=subprocess.STDOUT,
			)
		PLACES = build_places()
		for _ in range(90):
			try:
				graphql('{__typename}', timeout=2)
				break
			except Exception:
				if child and child.poll() is not None:
					raise RuntimeError(
						'OTP failed to start; inspect route_checker/otp.log'
					)
				time.sleep(1)
		else:
			raise RuntimeError('OTP startup timed out; inspect otp.log')
		print(f'Route checker: http://127.0.0.1:{a.port}', flush=True)
		ThreadingHTTPServer(('127.0.0.1', a.port), Handler).serve_forever()
	except KeyboardInterrupt:
		pass
	finally:
		if child:
			child.terminate()
			try:
				child.wait(timeout=10)
			except subprocess.TimeoutExpired:
				child.kill()
		if log:
			log.close()


if __name__ == '__main__':
	main()
