#!/usr/bin/env python3
"""Read-only surface timing/shape regressions against a running candidate OTP."""
import argparse
import csv
import io
import json
import zipfile
import server


def run():
	with zipfile.ZipFile(server.ROOT / 'data/generated/hk-transit-EXPERIMENTAL.gtfs.zip') as z:
		stops = {r['stop_id']: r for r in csv.DictReader(io.StringIO(z.read('stops.txt').decode('utf-8-sig')))}
	def point(sid):
		return dict(lat=float(stops[sid]['stop_lat']), lon=float(stops[sid]['stop_lon']))
	cases = [('690S', '8146', '18', '07:43', 25, 38), ('10P', '20014890', '20000008', '08:10', 8, 18)]
	results = []
	for route, origin, destination, departure, lower, upper in cases:
		data = server.plan(dict(origin=point(origin), destination=point(destination),
			departure='2026-09-30T' + departure + ':00+08:00', modes=['bus'],
			preference='fastest', includeAlternatives=False, maxResults=10))
		matches = [l for i in data['itineraries'] for l in i['legs'] if (l.get('route') or {}).get('shortName') == route]
		assert matches, 'No ' + route + ' option returned'
		leg = matches[0]
		assert lower <= leg['duration'] / 60 <= upper, (route, leg['duration'])
		assert leg['provenance']['timingModelKind'] == 'csdi_route_distance'
		assert len(leg['legGeometry']['points']) > 100, 'Missing road-following polyline'
		result = dict(route=route, minutes=round(leg['duration'] / 60, 2),
			departure=leg['start'], arrival=leg['end'], distance_m=round(leg['distance']),
			encoded_geometry_characters=len(leg['legGeometry']['points']))
		results.append(result)
		print('PASS:', json.dumps(result), flush=True)
	return results


if __name__ == '__main__':
	p = argparse.ArgumentParser(description=__doc__)
	p.add_argument('--otp-port', type=int, default=8193)
	a = p.parse_args()
	server.OTP = f'http://127.0.0.1:{a.otp_port}/otp/gtfs/v1'
	run()
