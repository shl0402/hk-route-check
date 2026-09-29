#!/usr/bin/env python3
"""Read-only September 2026 routing regressions against an already running OTP.

Uses this checkout's adapter and feed; never writes accounts, jobs or history.
Run after rebuilding the feed AND graph. The fixed times belong to the cached
September operator responses, not a claim about future/live service.
"""

import argparse
import csv
import io
import json
from pathlib import Path
import zipfile

import server


def check_feed():
	with zipfile.ZipFile(server.ROOT / 'data/generated/hk-transit-EXPERIMENTAL.gtfs.zip') as feed:
		def rows(name):
			return list(csv.DictReader(io.TextIOWrapper(feed.open(name), encoding='utf-8-sig')))

		stops = {s['stop_id']: s for s in rows('stops.txt')}
		trips = rows('trips.txt')
		routes = {r['route_id'] for r in rows('routes.txt')}
		proof = json.loads(feed.read('mtr_api_provenance.json'))
		checked = 0
		for trip in trips:
			key = proof['od_trips'].get(trip['trip_id'], {}).get('journey', '')
			if not key.startswith('JOURNEY:'):
				continue
			seconds = proof['od_journeys'][key]['api_total_seconds']
			if not trip['route_id'].endswith(f':PATH:{seconds}') or trip['route_id'] not in routes:
				raise AssertionError(f"Missing duration-separated pattern: {trip['trip_id']}")
			checked += 1
		if not checked:
			raise AssertionError('No whole-journey MTR trips found')
		for row in rows('transfers.txt'):
			if (row['transfer_type'] == '3'
				and row['from_stop_id'].startswith('RAIL:MTR:')
				and row['to_stop_id'].startswith('RAIL:MTR:')):
				raise AssertionError('Synthetic MTR transfer bans must be enforced by the API')
	print(f'PASS: {checked} MTR trips have separated duration patterns', flush=True)
	return stops


def check_routes(weekday, saturday):
	stops = check_feed()

	def point(sid):
		s = stops[f'RAIL:MTR:{sid}']
		return dict(lat=float(s['stop_lat']), lon=float(s['stop_lon']))

	lohas = dict(lat=22.29559, lon=114.26873)
	hku = dict(lat=22.2839758, lon=114.1355067)
	# Include other stations to catch effects outside the LOHAS Park branch.
	cases = [
		('LOHAS Park → HKU, weekday 11am', lohas, hku, weekday + 'T11:00', '57', '82', 2220, True),
		('LOHAS Park → HKU, Saturday 11am', lohas, hku, saturday + 'T11:00', '57', '82', 2220, False),
		('HKU → LOHAS Park, weekday 11am', hku, lohas, weekday + 'T11:00', '82', '57', 2220, False),
		('LOHAS Park → HKU, Saturday 07:32', lohas, hku, saturday + 'T07:32', '57', '82', 2220, False),
		('Mong Kok → North Point', point('6:TWL'), point('31:ISL'), weekday + 'T10:00', '6', '31', 1320, False),
		('Tsuen Wan → Yau Ma Tei', point('22:TWL'), point('5:TWL'), weekday + 'T10:00', '22', '5', 1080, False),
		('North Point → Central', point('31:ISL'), point('1:ISL'), weekday + 'T10:00', '31', '1', 780, False),
	]
	results = []
	for label, origin, destination, departure, first, last, seconds, alternatives in cases:
		data = server.plan(dict(
			origin=origin, destination=destination, departure=departure + ':00+08:00',
			modes=list(server.GROUPS) if alternatives else ['mtr'],
			preference='fastest', includeAlternatives=alternatives, maxResults=6,
		))
		matches = []
		for itinerary in data['itineraries']:
			if server.has_split_mtr_journey(itinerary):
				raise AssertionError(f'Split synthetic MTR journey: {label}')
			for leg in itinerary['legs']:
				journey = leg.get('provenance', {}).get('journeyTiming', {})
				ids = journey.get('stationIds', [])
				if ids and (ids[0], ids[-1]) == (first, last) and leg['duration'] == seconds:
					if (first, last) == ('57', '82'):
						if not any('Tseung Kwan O' in s['text'] for s in leg['journeyInstructions']):
							raise AssertionError(f'Missing TKO change: {label}')
					matches.append(itinerary)
		if not matches:
			raise AssertionError(f'Missing cached {seconds // 60}-minute MTR journey: {label}')
		best = matches[0]
		if alternatives:
			if data['itineraries'][0] not in matches:
				raise AssertionError('Fastest LOHAS–HKU rail route is not ranked first')
			if not any(any(l['mode'] in ('BUS', 'COACH') for l in it['legs']) for it in data['itineraries']):
				raise AssertionError('Bus alternatives were lost')
		result = dict(case=label, rail_minutes=seconds / 60,
			total_minutes=round(best['duration'] / 60, 1), options=len(data['itineraries']))
		results.append(result)
		print('PASS: ' + json.dumps(result, ensure_ascii=False), flush=True)
	return results


if __name__ == '__main__':
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument('--otp-port', type=int, default=8081)
	parser.add_argument('--weekday', default='2026-09-29')
	parser.add_argument('--saturday', default='2026-09-26')
	parser.add_argument('--output', type=Path, help='Optional JSON report')
	args = parser.parse_args()
	server.OTP = f'http://127.0.0.1:{args.otp_port}/otp/gtfs/v1'
	results = check_routes(args.weekday, args.saturday)
	if args.output:
		args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + '\n')
