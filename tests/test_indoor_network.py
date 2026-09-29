"""Protect against wrong-floor shortcuts and source restrictions, without downloads."""
import sys
import json
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import indoor_network as indoor
from indoor_matching import choose_entrance, platform_codes, map_platform_codes, platform_evidence
from indoor_timing import pathway_seconds, write_otp_config, LIFT_WAIT_SECONDS


def segment(pid, a, b, **values):
	props = dict(PedestrianRouteID=pid, FeatureType=1, Enabled=1, Direction=0, AccessTimeID=None)
	props.update(values)
	return dict(properties=props, geometry=dict(type='LineString', coordinates=[a, b]))


class IndoorNetworkTests(unittest.TestCase):
	def test_lift_wait_is_charged_once_by_otp_not_also_in_feed(self):
		# Six metres vertically: six seconds moving plus one 20-second wait.
		edges, _, _ = indoor.make_graph([segment(1, (114.2, 22.3, 0), (114.2, 22.3, 6), FeatureType=10)])
		self.assertEqual(pathway_seconds(edges[0]), 6)
		self.assertEqual(pathway_seconds(edges[0]) + LIFT_WAIT_SECONDS, edges[0]['seconds'])
		self.assertEqual(pathway_seconds(dict(mode=1, seconds=26)), 26)
		# OTP treats zero traversal_time as missing and substitutes a floor-hop
		# estimate. Keep same-height source lift connectors at one second.
		self.assertEqual(pathway_seconds(dict(mode=5, seconds=20)), 1)

	def test_otp_build_and_runtime_share_wait_and_preserve_other_settings(self):
		with tempfile.TemporaryDirectory() as folder:
			graph = Path(folder)
			(graph / 'build-config.json').write_text(json.dumps({'transitServiceStart': '2026-09-17'}))
			(graph / 'router-config.json').write_text(json.dumps({'routingDefaults': {'walk': {'speed': 1.3}}}))
			write_otp_config(graph)
			build = json.loads((graph / 'build-config.json').read_text())
			router = json.loads((graph / 'router-config.json').read_text())
			self.assertEqual(build['transitServiceStart'], '2026-09-17')
			self.assertEqual(router['routingDefaults']['walk']['speed'], 1.3)
			self.assertEqual(build['transferRequests'][0]['modes'], 'WALK')
			self.assertEqual(build['transferRequests'][0]['elevator']['boardSlack'], 'PT20S')
			self.assertEqual(router['routingDefaults']['elevator'], build['transferRequests'][0]['elevator'])

	def setUp(self):
		self.a = (114.2, 22.3, 0)
		self.b = (114.2001, 22.3, 0)
		self.c = (114.2001, 22.3, 6)

	def test_crossing_floors_are_not_connected(self):
		_, graph, _ = indoor.make_graph([
			segment(1, self.a, self.b),
			segment(2, self.c, (114.2002, 22.3, 6)),
		])
		self.assertIsNone(indoor.shortest(graph, self.a, self.c))

	def test_source_lift_connects_floors_with_estimated_wait(self):
		_, graph, _ = indoor.make_graph([
			segment(1, self.a, self.b),
			segment(2, self.b, self.c, FeatureType=10),
		])
		result = indoor.shortest(graph, self.a, self.c)
		self.assertIsNotNone(result)
		self.assertGreaterEqual(result[0], 26)

	def test_same_floor_lift_approaches_are_not_extra_rides(self):
		exit_point = (114.2002, 22.3, 6)
		edges, graph, _ = indoor.make_graph([
			segment(1, self.a, self.b, FeatureType=10),
			segment(2, self.b, self.c, FeatureType=10),
			segment(3, self.c, exit_point, FeatureType=10),
		])
		self.assertEqual([e['mode'] for e in edges], [1, 5, 1])
		seconds, path = indoor.shortest(graph, self.a, exit_point)
		self.assertEqual(sum(pathway_seconds(e) for e in path) + LIFT_WAIT_SECONDS, seconds)
		self.assertEqual({e['source_id'] for e in path}, {1, 2, 3})

	def test_escalator_direction_is_respected(self):
		for direction, start, end in ((1, self.b, self.c), (-1, self.c, self.b)):
			_, graph, _ = indoor.make_graph([segment(1, self.b, self.c, FeatureType=8, Direction=direction)])
			self.assertIsNotNone(indoor.shortest(graph, start, end))
			self.assertIsNone(indoor.shortest(graph, end, start))

	def test_closed_timed_unknown_paths_fail_closed(self):
		for values in (dict(Enabled=2), dict(AccessTimeID=123), dict(Direction=9), dict(FeatureType=999)):
			edges, graph, rejected = indoor.make_graph([segment(1, self.a, self.b, **values)])
			self.assertFalse(edges)
			self.assertTrue(rejected)
			self.assertIsNone(indoor.shortest(graph, self.a, self.b))

	def test_point_only_lift_is_not_invented(self):
		f = segment(1, self.b, self.c, FeatureType=10)
		f['geometry'] = dict(type='Point', coordinates=self.b)
		edges, _, rejected = indoor.make_graph([f])
		self.assertFalse(edges)
		self.assertIn('missing second endpoint', rejected[0]['reason'])

	def test_dijkstra_uses_duration_and_direction(self):
		_, graph, _ = indoor.make_graph([
			segment(1, self.a, self.b), segment(2, self.b, self.c, FeatureType=10),
			segment(3, self.a, self.c, FeatureType=12),
		])
		seconds, path = indoor.shortest(graph, self.a, self.c)
		self.assertEqual([e['source_id'] for e in path], [3])
		self.assertGreater(seconds, 0)

	def test_exit_labels_require_named_exit(self):
		self.assertEqual(indoor.exit_code('Exit C2'), 'C2')
		self.assertIsNone(indoor.exit_code('Concourse entry'))

	def test_island_line_does_not_match_south_island(self):
		self.assertEqual(indoor.named_lines('Platform 3 South Island Line to South Horizons'), {'SIL'})
		self.assertEqual(indoor.named_lines('Platform 2 Island Line to Kennedy Town'), {'ISL'})

	def test_continuous_lift_has_one_wait(self):
		middle = (self.b[0], self.b[1], 3)
		edges, graph, _ = indoor.make_graph([
			segment(1, self.b, middle, FeatureType=10),
			segment(2, middle, self.c, FeatureType=10),
		])
		self.assertEqual(len(edges), 1)
		self.assertEqual(indoor.shortest(graph, self.b, self.c)[0], 26)
		self.assertEqual(edges[0]['source_ids'], [1, 2])

	def test_lift_landing_with_walkway_is_preserved(self):
		middle = (self.b[0], self.b[1], 3)
		edges, _, _ = indoor.make_graph([
			segment(1, self.b, middle, FeatureType=10),
			segment(2, middle, self.c, FeatureType=10),
			segment(3, middle, (114.2002, 22.3, 3)),
		])
		self.assertEqual(len(edges), 3)

	def test_entrance_ground_marker_wins_over_concourse(self):
		stop = dict(stop_name='Station-A Access', stop_lon=114.2, stop_lat=22.3)
		def point(level, z):
			return dict(properties=dict(amenity_category='entry', amenity_name_en='Exit A', level_name_en=level),
				geometry=dict(coordinates=[114.2, 22.3, z]))
		ground, concourse = point('Exit A Ground Level', 7.91), point('Concourse Level', 1.57)
		selected, error = choose_entrance(stop, [concourse, ground], indoor.horizontal)
		self.assertIs(selected, ground)
		self.assertIsNone(error)
		self.assertIsNone(choose_entrance(stop, [ground, point('Ground Level', 8)], indoor.horizontal)[0])

	def test_platform_codes_do_not_parse_arbitrary_instructions(self):
		self.assertEqual(platform_codes('1 or 2'), ('1', '2'))
		self.assertEqual(platform_codes('2'), ('2',))
		self.assertEqual(platform_codes('Platform 2 towards Exit 3'), ())
		self.assertEqual(map_platform_codes('Platform1 Tuen Ma Line'), ('1',))
		self.assertEqual(map_platform_codes('Platform 3, 4 East Rail Line'), ('3', '4'))

	def test_millimetre_endpoint_gap_is_repaired_but_not_floor_gap(self):
		near = (self.b[0]+.00000002,self.b[1],self.b[2])
		_, graph, _ = indoor.make_graph([segment(1,self.a,self.b),segment(2,near,self.c,FeatureType=12)])
		self.assertIsNotNone(indoor.shortest(graph,self.a,self.c))
		near = (self.b[0],self.b[1],self.b[2]+.5)
		_, graph, _ = indoor.make_graph([segment(1,self.a,self.b),segment(2,near,self.c,FeatureType=12)])
		self.assertIsNone(indoor.shortest(graph,self.a,self.c))

	def test_fullwidth_exit_and_anonymous_ground_lift(self):
		stop=dict(stop_name='Station-C Access',stop_lon=114.2,stop_lat=22.3)
		entry=dict(properties=dict(amenity_category='entry',amenity_name_en='Exit Ｃ',level_name_en='Concourse Level'),geometry=dict(coordinates=self.a))
		self.assertIs(choose_entrance(stop,[entry],indoor.horizontal)[0],entry)
		lift=dict(properties=dict(amenity_category='elevator',amenity_name_en='Elevator',level_name_en='Exit Ground Level'),geometry=dict(coordinates=self.a))
		stop.update(stop_name='Station Access',source_subcat='MTRLIF')
		self.assertIs(choose_entrance(stop,[lift],indoor.horizontal)[0],lift)
		stop['source_subcat']=None
		self.assertIsNone(choose_entrance(stop,[lift],indoor.horizontal)[0])


	def test_operator_track_evidence_is_station_and_line_scoped(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			raw = root / 'data/mtr_api/raw'
			raw.mkdir(parents=True)
			(raw.parent / 'inventory.json').write_text(json.dumps({'HR': {'metadata': {'lines': [
				{'ID': 1, 'alias': 'TKL'}, {'ID': 2, 'alias': 'ISL'}]}}}))
			def node(station, kind, platform=None, line=1):
				return dict(ID=station, lineID=line, Track=1, linkType=kind, platform=platform)
			def write(name, nodes):
				(raw / name).write_text(json.dumps(dict(status='ok', response=dict(routes=[dict(path=nodes)]))))
			write('HR_a.json', [node(10,'RIDE','1'),node(20,'END')])
			write('HR_b.json', [node(20,'RIDE','2'),node(10,'END')])
			write('HR_c.json', [node(10,'RIDE','4',2),node(30,'END',line=2)])
			departures, arrivals, proof = platform_evidence(root)
			self.assertEqual(departures[('RAIL:MTR:10:TKL','20')], ('1',))
			self.assertEqual(arrivals[('RAIL:MTR:20:TKL','10')], ('2',))
			self.assertEqual(arrivals[('RAIL:MTR:10:TKL','20')], ('1',))
			self.assertEqual(len(proof['files']), 3)
			# Conflicting observations must withhold that platform, not pick one.
			write('HR_d.json', [node(20,'RIDE','3'),node(10,'END')])
			departures, arrivals, _ = platform_evidence(root)
			self.assertEqual(departures[('RAIL:MTR:20:TKL','10')], ())
			self.assertEqual(arrivals[('RAIL:MTR:20:TKL','10')], ())


if __name__ == '__main__':
	unittest.main()
