import json, unittest, urllib.request
from unittest.mock import patch
import server


class SplitTests(unittest.TestCase):
	def test_independent_routing_filters(self):
		with patch.object(
			server,
			'tram_route_groups',
			return_value={'tram': ['F:4001'], 'light_rail': ['F:RAIL:LRT:705']},
		):
			self.assertEqual(
				server.separate_tram_filters(['tram']),
				[{'exclude': [{'routes': ['F:RAIL:LRT:705']}]}],
			)
			self.assertEqual(
				server.separate_tram_filters(['light_rail']),
				[{'exclude': [{'routes': ['F:4001']}]}],
			)
			self.assertEqual(server.separate_tram_filters(['tram', 'light_rail']), [])
			self.assertEqual(server.separate_tram_filters(['mtr']), [])

	def request(self, modes, origin, destination):
		data = dict(
			modes=modes,
			origin=dict(lat=origin[0], lon=origin[1]),
			destination=dict(lat=destination[0], lon=destination[1]),
			preference='walking',
			departure='2026-09-21T10:00',
		)
		req = urllib.request.Request(
			'http://127.0.0.1:8000/api/route',
			data=json.dumps(data).encode(),
			headers={'Content-Type': 'application/json'},
		)
		with urllib.request.urlopen(req, timeout=100) as r:
			return json.load(r)

	def test_island_tram_label(self):
		r = self.request(['tram'], (22.2904, 114.2005), (22.2876, 114.1358))
		legs = [l for it in r['itineraries'] for l in it['legs'] if l['transitLeg']]
		self.assertTrue(legs)
		self.assertTrue(
			all(
				l['transportGroup'] == 'tram' and l['transportLabel'] == 'Tram'
				for l in legs
			)
		)

	def test_tram_only_excludes_light_rail(self):
		r = self.request(['tram'], (22.448, 114.0046), (22.4627, 114.0016))
		self.assertTrue(r['itineraries'])
		self.assertFalse(
			any(
				l.get('transportGroup') == 'light_rail'
				for it in r['itineraries']
				for l in it['legs']
			)
		)

	def test_light_rail_only_excludes_island_tram(self):
		r = self.request(['light_rail'], (22.2904, 114.2005), (22.2876, 114.1358))
		self.assertFalse(
			any(
				l.get('transportGroup') == 'tram'
				for it in r['itineraries']
				for l in it['legs']
			)
		)


if __name__ == '__main__':
	unittest.main(verbosity=2)
