import unittest, copy, json, urllib.request, urllib.error, os
import server

TEST_URL = os.environ.get('ROUTE_CHECKER_TEST_URL', 'http://127.0.0.1:8000')
BASE = dict(
	origin={'lat': 22.2819, 'lon': 114.1589},
	destination={'lat': 22.3194, 'lon': 114.1694},
	modes=['mtr', 'bus', 'ferry', 'tram', 'funicular'],
	preference='fastest',
	departure='2026-09-17T10:00',
)


def leg(start, end, transit):
	return dict(
		mode='BUS' if transit else 'WALK',
		start={'scheduledTime': '2026-09-17T' + start + '+08:00'},
		end={'scheduledTime': '2026-09-17T' + end + '+08:00'},
		transitLeg=transit,
		duration=server.timestamp('2026-09-17T' + end + '+08:00')
		- server.timestamp('2026-09-17T' + start + '+08:00'),
		route=None,
		**{'from': {}, 'to': {}}
	)


class TimingTests(unittest.TestCase):
	def test_only_first_wait_removed(self):
		it = dict(
			start='2026-09-17T10:00:00+08:00',
			duration=1800,
			legs=[
				leg('10:00:00', '10:05:00', False),
				leg('10:10:00', '10:20:00', True),
				leg('10:25:00', '10:30:00', True),
			],
		)
		r = server.normalize(it)
		self.assertEqual(r['initialWaitExcludedSeconds'], 300)
		self.assertEqual(r['transferWaitSeconds'], 300)
		self.assertEqual(r['displayDurationSeconds'], 1500)
		self.assertEqual(r['transfers'], 1)

	def test_split_mtr_journeys_are_rejected(self):
		rail = {'transitLeg': True, 'route': {'gtfsId': 'test:RAIL:MTR:TWL'}}
		walk = {'transitLeg': False}
		bus = {'transitLeg': True, 'route': {'gtfsId': 'test:74X'}}
		self.assertTrue(server.has_split_mtr_journey({'legs': [rail, walk, rail]}))
		self.assertFalse(server.has_split_mtr_journey({'legs': [walk, rail, walk]}))
		self.assertFalse(server.has_split_mtr_journey({'legs': [rail, bus, rail]}))

	def test_walk_only_no_subtraction(self):
		r = server.normalize(
			dict(
				start='2026-09-17T10:00:00+08:00',
				duration=300,
				legs=[leg('10:00:00', '10:05:00', False)],
			)
		)
		self.assertEqual(r['displayDurationSeconds'], 300)

	def test_first_transit_no_gap(self):
		r = server.normalize(
			dict(
				start='2026-09-17T10:00:00+08:00',
				duration=300,
				legs=[leg('10:00:00', '10:05:00', True)],
			)
		)
		self.assertEqual(r['initialWaitExcludedSeconds'], 0)

	def test_reject_bad_coordinates(self):
		d = copy.deepcopy(BASE)
		d['origin']['lat'] = float('nan')
		with self.assertRaises(ValueError):
			server.validate_request(d)

	def test_reject_unavailable_date(self):
		d = {**BASE, 'departure': '2027-01-01T10:00'}
		with self.assertRaises(ValueError):
			server.validate_request(d)

	def test_reject_car_mode(self):
		with self.assertRaises(ValueError):
			server.validate_request({**BASE, 'modes': ['car']})


class LiveTests(unittest.TestCase):
	def request(self, data):
		req = urllib.request.Request(
			TEST_URL + '/api/route',
			data=json.dumps(data).encode(),
			headers={'Content-Type': 'application/json'},
		)
		with urllib.request.urlopen(req, timeout=100) as r:
			return json.load(r)

	def test_city_mtr_and_wait(self):
		r = self.request({**BASE, 'modes': ['mtr']})
		self.assertTrue(r['itineraries'])
		for it in r['itineraries']:
			self.assertTrue(
				all(l['mode'] in ['WALK', 'SUBWAY', 'RAIL'] for l in it['legs'])
			)
			self.assertEqual(
				it['duration'] - it['initialWaitExcludedSeconds'],
				it['displayDurationSeconds'],
			)

	def test_walk_only(self):
		d = {**BASE, 'destination': {'lat': 22.284, 'lon': 114.158}, 'modes': []}
		r = self.request(d)
		self.assertTrue(r['itineraries'])
		self.assertTrue(
			all(l['mode'] == 'WALK' for it in r['itineraries'] for l in it['legs'])
		)

	def test_ferry(self):
		d = {
			**BASE,
			'origin': {'lat': 22.2874, 'lon': 114.1579},
			'destination': {'lat': 22.2082, 'lon': 114.0285},
			'modes': ['ferry'],
		}
		r = self.request(d)
		self.assertTrue(r['itineraries'])
		self.assertTrue(
			any(l['mode'] == 'FERRY' for it in r['itineraries'] for l in it['legs'])
		)

	def test_live_internal_mtr_change_is_included(self):
		r = self.request(
			{
				**BASE,
				'origin': {'lat': 22.2882, 'lon': 114.2097},
				'destination': {'lat': 22.2983, 'lon': 114.1722},
				'modes': ['mtr'],
			}
		)
		it = next(
			i
			for i in r['itineraries']
			if any(l.get('internalTransfers', 0) > 0 for l in i['legs'])
		)
		rail = [l for l in it['legs'] if l['transitLeg']]
		self.assertEqual(len(rail), 1)
		self.assertGreater(rail[0].get('internalTransfers', 0), 0)
		self.assertEqual(it['transfers'], 0)
		self.assertEqual(it['transferWaitSeconds'], 0)
		self.assertGreater(it['initialWaitExcludedSeconds'], 0)
		self.assertEqual(
			it['displayDurationSeconds'],
			it['duration'] - it['initialWaitExcludedSeconds'],
		)

	def test_isl_source_is_not_claimed_as_live_wait(self):
		r = self.request(
			{
				**BASE,
				'origin': {'lat': 22.2882, 'lon': 114.2097},
				'destination': {'lat': 22.2983, 'lon': 114.1722},
				'modes': ['mtr'],
			}
		)
		p = next(
			l['provenance']
			for it in r['itineraries']
			for l in it['legs']
			if l['provenance'].get('line') == 'Island Line'
		)
		self.assertEqual(p['sourceType'], 'rail_wiki')
		self.assertTrue(all(0 < x['headwaySeconds'] < 720 for x in p['feedIntervals']))
		self.assertIn('hkrail.fandom.com', p['sourceUrl'])
		self.assertIn('oldid=', p['sourceUrl'])
		self.assertTrue(any(x['minutes'] == '3.6-5' for x in p['publishedIntervals']))
		self.assertFalse(any('overstates normal daytime' in x for x in p['warnings']))
		self.assertTrue(any('effective date is not stated' in x for x in p['warnings']))
		self.assertGreater(p['journeyTiming']['seconds'], 0)
		self.assertIn('not a live arrival', p['waitingExplanation'])
		self.assertEqual(r['sources']['search']['transferPreferenceCost'], 0)

	def test_wiki_bus_provenance(self):
		r = self.request(
			{
				**BASE,
				'origin': {'lat': 22.31782, 'lon': 114.26918},
				'destination': {'lat': 22.30180, 'lon': 114.17844},
				'modes': ['bus'],
				'departure': '2026-09-21T10:00',
			}
		)
		legs = [l for it in r['itineraries'] for l in it['legs'] if l['mode'] == 'BUS']
		wiki = [
			l['provenance'] for l in legs if l['provenance'].get('sourceType') == 'wiki'
		]
		self.assertTrue(
			wiki, 'The activated wiki graph must return attributed bus service'
		)
		for p in wiki:
			self.assertTrue(p['tripId'].startswith('WIKI:'))
			self.assertIn('oldid=', p['sourceUrl'])
			self.assertGreater(p['wikiRevision'], 0)
			self.assertIn('government stops/running times', p['sourceName'])
			self.assertIn('not a live', p['waitingExplanation'])

	def test_preferences(self):
		for pref in ['walking', 'transfers']:
			r = self.request({**BASE, 'preference': pref})
			self.assertTrue(r['itineraries'])
			self.assertEqual(r['ranking'], pref)

	def test_search(self):
		for q in ['Central', '%E4%B8%AD%E7%92%B0']:
			with urllib.request.urlopen(TEST_URL + '/api/search?q=' + q) as r:
				items = json.load(r)
			self.assertTrue(items)


if __name__ == '__main__':
	unittest.main(verbosity=2)
