"""Live acceptance checks for newly compiled MTR/LRT service families."""

import csv, io, json, zipfile, unittest, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RailServices(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		with zipfile.ZipFile(
			ROOT / 'data/generated/hk-transit-EXPERIMENTAL.gtfs.zip'
		) as z:
			cls.stops = {
				r['stop_id']: r
				for r in csv.DictReader(
					io.StringIO(z.read('stops.txt').decode('utf-8-sig'))
				)
			}

	def point(self, system, line, name):
		file, key = (
			('mtr_lines_and_stations.csv', 'Station ID')
			if system == 'MTR'
			else ('light_rail_routes_and_stops.csv', 'Stop ID')
		)
		with (ROOT / 'data/user_inputs/mtr' / file).open(
			encoding='utf-8-sig'
		) as handle:
			r = next(
				r
				for r in csv.DictReader(handle)
				if r['Line Code'] == line and r['English Name'] == name
			)
		stop = self.stops[f'RAIL:{system}:{r[key]}:{line}']
		return dict(lat=float(stop['stop_lat']), lon=float(stop['stop_lon']))

	def route(self, system, line, a, b, time='08:00'):
		body = dict(
			origin=self.point(system, line, a),
			destination=self.point(system, line, b),
			modes=['mtr' if system == 'MTR' else 'light_rail'],
			preference='transfers',
			departure='2026-09-21T' + time,
		)
		req = urllib.request.Request(
			'http://127.0.0.1:8000/api/route',
			data=json.dumps(body).encode(),
			headers={'Content-Type': 'application/json'},
		)
		with urllib.request.urlopen(req, timeout=100) as response:
			return json.load(response)

	def test_lohas_through_train(self):
		r = self.route('MTR', 'TKL', 'North Point', 'LOHAS Park')
		legs = [l for it in r['itineraries'] for l in it['legs'] if l['transitLeg']]
		self.assertTrue(
			any(
				l['provenance'].get('sourceType') == 'rail_wiki'
				and l['provenance'].get('tripId', '').startswith('RAILWIKI:TKL:')
				for l in legs
			)
		)
		self.assertTrue(
			any(
				it['transfers'] == 0 and any(l['transitLeg'] for l in it['legs'])
				for it in r['itineraries']
			)
		)

	def test_light_rail_uses_wiki_and_platform(self):
		r = self.route('LRT', '705', 'Tin Shui Wai', 'Tin Yuet', '10:00')
		ps = [
			l['provenance']
			for it in r['itineraries']
			for l in it['legs']
			if l['transitLeg']
		]
		self.assertTrue(ps)
		self.assertTrue(
			any(
				p.get('sourceType') == 'rail_wiki' and p.get('boardingPlatform')
				for p in ps
			)
		)
		self.assertTrue(
			all(f['headwaySeconds'] < 1500 for p in ps for f in p['feedIntervals'])
		)

	def test_disney_calendar_model_is_labelled(self):
		r = self.route('MTR', 'DRL', 'Sunny Bay', 'Disneyland Resort', '10:00')
		ps = [
			l['provenance']
			for it in r['itineraries']
			for l in it['legs']
			if l['transitLeg']
		]
		p = next(p for p in ps if p.get('tripId', '').startswith('RAILWIKI:DRL:'))
		self.assertEqual(p['timingModelKind'], 'conditional_envelope')
		self.assertIn('unverified', p['wikiStatus'])


if __name__ == '__main__':
	unittest.main(verbosity=2)
