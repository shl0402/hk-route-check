#!/usr/bin/env python3
"""Read-only checks of an already-running OTP and its matching indoor feed."""
import argparse
import csv
from collections import defaultdict
from itertools import zip_longest
import io
import json
from pathlib import Path
import zipfile

import server


def validate_feed(root):
	with zipfile.ZipFile(root / 'data/generated/hk-transit-EXPERIMENTAL.gtfs.zip') as archive:
		report = json.loads(archive.read('indoor_provenance.json'))
		stops = {s['stop_id']: s for s in csv.DictReader(io.StringIO(archive.read('stops.txt').decode()))}
		paths = list(csv.DictReader(io.StringIO(archive.read('pathways.txt').decode())))
		assert len(paths) == report['pathways']
		for p in paths:
			assert p['from_stop_id'] in stops and p['to_stop_id'] in stops
			assert int(p['traversal_time']) > 0
			assert p['is_bidirectional'] in ('0', '1')
			assert stops[p['from_stop_id']]['parent_station'] == stops[p['to_stop_id']]['parent_station']
		lohas = next(s for s in report['stations'] if s['name'] == 'LOHAS Park Station')
		assert lohas['status'] == 'activated', lohas['reasons']
		# Check actual GTFS topology, not only the compiler's own summary.
		graph, reverse = defaultdict(set), defaultdict(set)
		for p in paths:
			x, y = p['from_stop_id'], p['to_stop_id']
			graph[x].add(y); reverse[y].add(x)
			if p['is_bidirectional'] == '1':
				graph[y].add(x); reverse[x].add(y)
		def reachable(start, edges):
			seen, todo = {start}, [start]
			while todo:
				for nxt in edges[todo.pop()] - seen:
					seen.add(nxt); todo.append(nxt)
			return seen
		platforms = [sid for sid in stops if ':AREA:' in sid]
		for sid in platforms:
			entrances = {x for x in reachable(sid, graph) if stops[x]['location_type'] == '2'}
			assert entrances & reachable(sid, reverse), sid
		for route in lohas['connections']:
			assert 0 < route['estimated_seconds'] < 600
			assert route['source_segments']
			assert route['distance_m'] < 500
		covered = report['activated_stations'] + report.get('partial_stations', 0)
		print(f"PASS: {covered} covered stations; {len(paths)} pathways; {len(platforms)} platforms have bidirectional entrance access", flush=True)
	return report, stops


def validate_timetable(root, base):
	with zipfile.ZipFile(base) as before, zipfile.ZipFile(root / 'data/generated/hk-transit-EXPERIMENTAL.gtfs.zip') as after:
		unchanged = [name for name in before.namelist() if name.endswith('.txt')
			and name not in ('stops.txt', 'levels.txt', 'pathways.txt', 'stop_times.txt')]
		for name in unchanged:
			assert before.read(name) == after.read(name), name
		def rows(archive):
			return csv.DictReader(io.TextIOWrapper(archive.open('stop_times.txt'), encoding='utf-8-sig'))
		count = mapped = 0
		for old, new in zip_longest(rows(before), rows(after)):
			assert old is not None and new is not None
			count += 1
			if old['stop_id'] != new['stop_id']:
				assert old['stop_id'].startswith('RAIL:MTR:') and ':AREA:' in new['stop_id']
				mapped += 1
			assert {k:v for k,v in old.items() if k != 'stop_id'} == {k:v for k,v in new.items() if k != 'stop_id'}
		proof = json.loads(after.read('indoor_provenance.json'))
		assert mapped == proof['mapped_stop_calls']
		print(f"PASS: {count} stop-time rows retain all timing fields; {mapped} verified platform assignments; {len(unchanged)} other tables unchanged", flush=True)


def check(root, departure):
	report, stops = validate_feed(root)
	def place(sid):
		s = stops[sid]
		return dict(lat=float(s['stop_lat']), lon=float(s['stop_lon']))
	hku = dict(lat=22.2839758, lon=114.1355067)
	results = []
	for label, sid, reverse in (
		('LOHAS C1 → HKU', 'LANDSD:ACCESS:556852', False),
		('LOHAS C2 → HKU', 'LANDSD:ACCESS:562032', False),
		('HKU → LOHAS C1', 'LANDSD:ACCESS:556852', True),
	):
		origin, dest = (hku, place(sid)) if reverse else (place(sid), hku)
		data = server.plan(dict(origin=origin, destination=dest, departure=departure,
			modes=['mtr'], preference='fastest', includeAlternatives=False, maxResults=6))
		candidates = []
		for itinerary in data['itineraries']:
			if any(leg['transitLeg'] and leg['duration'] == 2220 for leg in itinerary['legs']):
				candidates.append(itinerary)
		assert candidates, 'Missing 37-minute cached rail journey: ' + label
		best = min(candidates, key=lambda x: x['duration'])
		walks = [leg for leg in best['legs'] if leg['mode'] == 'WALK']
		access = walks[-1] if reverse else walks[0]
		assert access['distance'] < 650, (label, access)
		assert access['duration'] < 720, (label, access)
		assert 'LandsD' in access['provenance']['sourceName'], access['provenance']
		result = dict(case=label, access_metres=round(access['distance']),
			access_minutes=round(access['duration'] / 60, 1), total_minutes=round(best['duration'] / 60, 1))
		results.append(result)
		print('PASS:', json.dumps(result), flush=True)
	return results


def check_directions(root, departure):
	report, stops = validate_feed(root)
	def point(station):
		row=next(s for s in report['stations'] if s['name']==station+' Station')
		sid=next(x for x in row['bindings'] if x.startswith('INDOOR:ACCESS'))
		s=stops[sid];return dict(lat=float(s['stop_lat']),lon=float(s['stop_lon']))
	cases=[('Hang Hau','Po Lam','51','52','1',None),('Hang Hau','Tseung Kwan O','51','50','2',None),('Po Lam','Hang Hau','52','51',None,'2'),('Tseung Kwan O','Hang Hau','50','51',None,'1'),('Tsing Yi','Hong Kong','42','39','4','3/4'),('Hong Kong','Tsing Yi','39','42','3/4','3')]
	results=[]
	for origin,dest,first,last,expected_from,expected_to in cases:
		response=server.plan(dict(origin=point(origin),destination=point(dest),departure=departure,modes=['mtr'],preference='fastest',includeAlternatives=False,maxResults=6))
		matches=[]
		for itinerary in response['itineraries']:
			for leg in itinerary['legs']:
				ids=leg.get('provenance',{}).get('journeyTiming',{}).get('stationIds',[])
				if ids and ids[0]==first and ids[-1]==last:
					a=leg['from'].get('stop',{}).get('gtfsId','').split(':',1)[-1]
					b=leg['to'].get('stop',{}).get('gtfsId','').split(':',1)[-1]
					assert ':AREA:' in a and ':AREA:' in b,(origin,dest,a,b)
					if expected_from:assert stops[a]['platform_code']==expected_from,(origin,dest,stops[a]['platform_code'])
					if expected_to:assert stops[b]['platform_code']==expected_to,(origin,dest,stops[b]['platform_code'])
					matches.append((a,b))
		assert matches,(origin,dest,'no matching journey')
		result=dict(origin=origin,destination=dest,from_platform=stops[matches[0][0]]['platform_code'],to_platform=stops[matches[0][1]]['platform_code'])
		results.append(result);print('PASS',json.dumps(result),flush=True)
	return results


if __name__ == '__main__':
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument('--root', type=Path, default=server.ROOT)
	parser.add_argument('--otp-port', type=int, default=8081)
	parser.add_argument('--departure', default='2026-09-29T11:00:00+08:00')
	parser.add_argument('--feed-only', action='store_true')
	parser.add_argument('--directions', action='store_true', help='Also check physical platforms in both directions')
	parser.add_argument('--base', type=Path, help='Also verify timetable preservation against the pre-indoor feed')
	args = parser.parse_args()
	server.ROOT = args.root.resolve()
	if args.base:
		validate_timetable(server.ROOT, args.base)
	server.OTP = f'http://127.0.0.1:{args.otp_port}/otp/gtfs/v1'
	if args.feed_only:
		validate_feed(server.ROOT)
	else:
		check(server.ROOT, args.departure)
		if args.directions:
			check_directions(server.ROOT, args.departure)
