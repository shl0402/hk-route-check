import csv, io, json, unittest, urllib.request, zipfile
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]


class DirectPairs(unittest.TestCase):
	def check_pair(self, line, origin, destination, expected):
		with zipfile.ZipFile(
			ROOT / 'data/generated/hk-transit-EXPERIMENTAL.gtfs.zip'
		) as z:
			stops = {
				r['stop_id']: r
				for r in csv.DictReader(
					io.TextIOWrapper(z.open('stops.txt'), encoding='utf-8-sig')
				)
			}

		def point(id):
			s = stops[f'RAIL:MTR:{id}:{line}']
			return dict(lat=float(s['stop_lat']), lon=float(s['stop_lon']))

		req = urllib.request.Request(
			'http://127.0.0.1:8000/api/route',
			data=json.dumps(
				dict(
					origin=point(origin),
					destination=point(destination),
					modes=['mtr'],
					preference='transfers',
					departure='2026-09-22T10:00',
				)
			).encode(),
			headers={'Content-Type': 'application/json'},
		)
		with urllib.request.urlopen(req, timeout=120) as f:
			data = json.load(f)
		matched = []
		for it in data['itineraries']:
			for prev, nxt in zip(it['legs'], it['legs'][1:]):
				self.assertGreaterEqual(
					datetime.fromisoformat(nxt['start']['scheduledTime']),
					datetime.fromisoformat(prev['end']['scheduledTime']),
				)
			for leg in it['legs']:
				p = leg['provenance']
				j = p.get('journeyTiming')
				if not j:
					continue
				self.assertEqual(leg['duration'], j['seconds'])
				self.assertFalse(p['runningTimeSegments'])
				self.assertGreater(len(leg['apiPath']), 1)
				if (
					j['line'] == line
					and j['stationIds'][0] == origin
					and j['stationIds'][-1] == destination
				):
					matched.append(leg)
		self.assertTrue(matched, (line, origin, destination, data))
		for leg in matched:
			self.assertEqual(leg['duration'], expected)
			self.assertIn(
				f'o={origin}&d={destination}', leg['provenance']['journeyTiming']['url']
			)

	def test_mong_kok_to_north_point_whole_journey(self):
		with zipfile.ZipFile(
			ROOT / 'data/generated/hk-transit-EXPERIMENTAL.gtfs.zip'
		) as z:
			stops = {
				r['stop_id']: r
				for r in csv.DictReader(
					io.TextIOWrapper(z.open('stops.txt'), encoding='utf-8-sig')
				)
			}

		def point(sid):
			s = stops[sid]
			return dict(lat=float(s['stop_lat']), lon=float(s['stop_lon']))

		body = dict(
			origin=point('RAIL:MTR:6:TWL'),
			destination=point('RAIL:MTR:31:ISL'),
			modes=['mtr'],
			preference='fastest',
			departure='2026-09-22T10:00',
		)
		req = urllib.request.Request(
			'http://127.0.0.1:8000/api/route',
			data=json.dumps(body).encode(),
			headers={'Content-Type': 'application/json'},
		)
		with urllib.request.urlopen(req, timeout=120) as f:
			data = json.load(f)
		matches = []
		for it in data['itineraries']:
			rail = [l for l in it['legs'] if l['transitLeg']]
			for l in rail:
				j = l['provenance'].get('journeyTiming', {})
				if (
					j.get('stationIds', [None])[0] == '6'
					and j['stationIds'][-1] == '31'
					and j['seconds'] == 1320
				):
					self.assertEqual(len(rail), 1)
					self.assertEqual(l['duration'], 1320)
					self.assertEqual(l['internalTransfers'], 1)
					self.assertEqual(it['transfers'], 0)
					self.assertEqual(it['transferWaitSeconds'], 0)
					self.assertTrue(
						any(
							'Admiralty' in step['text']
							for step in l['journeyInstructions']
						)
					)
					matches.append(l)
		self.assertTrue(matches, data)

	def test_lohas_park_hku_saturday_same_line_change(self):
		body = dict(origin={'lat':22.29559,'lon':114.26873},
			destination={'lat':22.2839758,'lon':114.1355067},
			modes=['mtr','bus','ferry','light_rail','tram','funicular'],
			preference='fastest', departure='2026-09-26T07:32')
		req = urllib.request.Request('http://127.0.0.1:8000/api/route',
			data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
		with urllib.request.urlopen(req,timeout=120) as response:
			data=json.load(response)
		matches=[]
		for it in data['itineraries']:
			for leg in it['legs']:
				j=leg['provenance'].get('journeyTiming',{})
				ids=j.get('stationIds',[])
				if ids and (ids[0],ids[-1])==('57','82') and j.get('seconds')==2220:
					self.assertNotIn('KTL',j['line'])
					self.assertEqual(leg['duration'],2220)
					self.assertEqual(it['transfers'],0)
					self.assertTrue(any('Tseung Kwan O' in step['text'] for step in leg['journeyInstructions']))
					matches.append(it)
		self.assertTrue(matches, data)
		self.assertLess(matches[0]['displayDurationSeconds'],60*60)

	def test_twl_22_to_5_is_eighteen_minutes(self):
		self.check_pair('TWL', '22', '5', 1080)

	def test_north_point_to_central_is_thirteen_minutes(self):
		self.check_pair('ISL', '31', '1', 780)


if __name__ == '__main__':
	unittest.main(verbosity=2)
