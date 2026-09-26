import copy, csv, io, json, unittest, zipfile, collections
import server


class Sources(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		with zipfile.ZipFile(
			server.ROOT / 'data/generated/hk-transit-MTR-API.gtfs.zip'
		) as z:
			cls.proof = json.loads(z.read('mtr_api_provenance.json'))

	def test_every_connection_matches_own_origin_destination(self):
		proof = self.proof
		pending = {}
		checked = 0
		lines = set()
		with zipfile.ZipFile(
			server.ROOT / 'data/generated/hk-transit-MTR-API.gtfs.zip'
		) as z:
			for r in csv.DictReader(
				io.TextIOWrapper(z.open('stop_times.txt'), encoding='utf-8-sig')
			):
				tid = r['trip_id']
				if tid not in proof['od_trips']:
					continue
				j = proof['od_journeys'][proof['od_trips'][tid]['journey']]
				lines.update(j['line'].split(' → '))

				def sec(v):
					h, m, s = map(int, v.split(':'))
					return 3600 * h + 60 * m + s

				if r['stop_sequence'] == '1':
					pending[tid] = r
				else:
					a = pending.pop(tid)
					self.assertEqual(r['stop_sequence'], '2')
					self.assertEqual(
						sec(r['arrival_time']) - sec(a['departure_time']),
						j['api_total_seconds'],
					)
					self.assertEqual(
						(a['stop_id'], r['stop_id']),
						(j['stop_ids'][0], j['stop_ids'][-1]),
					)
					self.assertEqual((a['pickup_type'], r['pickup_type']), ('0', '1'))
					checked += 1
		self.assertFalse(pending)
		self.assertEqual(checked, len(proof['od_trips']))
		self.assertEqual(len(lines), 10)

	def test_mong_kok_to_north_point_includes_change(self):
		matches = [
			j
			for j in self.proof['od_journeys'].values()
			if j['path'][0] == '6'
			and j['path'][-1] == '31'
			and j.get('internal_transfers') == 1
			and j['api_total_seconds'] == 1320
		]
		self.assertTrue(matches)
		self.assertEqual(matches[0]['interchanges'][0]['station_id'], '2')
		self.assertIn(
			'Interchange at Admiralty', matches[0]['interchanges'][0]['instruction']
		)

	def test_direct_pair_regressions(self):
		cases = {('TWL', '22', '5'): 1080, ('ISL', '31', '1'): 780}
		for (line, a, b), expected in cases.items():
			matches = [
				j
				for j in self.proof['od_journeys'].values()
				if j['line'] == line and j['path'][0] == a and j['path'][-1] == b
			]
			self.assertTrue(matches)
			self.assertTrue(all(j['api_total_seconds'] == expected for j in matches))

	def test_provenance_uses_direct_pair(self):
		original = server._SOURCE_CONTEXT
		try:
			c = copy.deepcopy(server.source_context())
			c['odtrips'] = self.proof['od_trips']
			c['odjourneys'] = self.proof['od_journeys']
			server._SOURCE_CONTEXT = c
			tid, od = next(
				(t, o)
				for t, o in c['odtrips'].items()
				if c['odjourneys'][o['journey']]['line'] == 'TWL'
				and c['odjourneys'][o['journey']]['path'][0] == '22'
				and c['odjourneys'][o['journey']]['path'][-1] == '5'
			)
			j = c['odjourneys'][od['journey']]
			leg = {
				'transitLeg': True,
				'route': {'gtfsId': 'test:RAIL:MTR:TWL'},
				'trip': {'gtfsId': 'test:' + tid},
				'from': {'stop': {'gtfsId': 'test:' + j['stop_ids'][0]}},
				'to': {'stop': {'gtfsId': 'test:' + j['stop_ids'][-1]}},
			}
			p = server.leg_provenance(leg)
			self.assertEqual(p['journeyTiming']['seconds'], 1080)
			self.assertIn('o=22&d=5', p['journeyTiming']['url'])
			self.assertEqual(p['runningTimeSegments'], [])
		finally:
			server._SOURCE_CONTEXT = original


if __name__ == '__main__':
	unittest.main()
