"""Missing bus times must use route distance without altering published anchors."""
from pathlib import Path
import sys
import unittest
from shapely.geometry import LineString, MultiLineString

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from surface_timing import interpolate, match_line, connected_source_line


def row(time='', departure=None):
	return dict(arrival_time=time, departure_time=time if departure is None else departure, timepoint='1' if time else '0')


class SurfaceTimingTests(unittest.TestCase):
	def test_express_hop_gets_distance_share_not_stop_share(self):
		result, count = interpolate([row('07:00:00'), row(), row(), row('07:30:00')], [0, 1000, 19000, 20000])
		self.assertEqual(result[1]['arrival_time'], '07:01:30')
		self.assertEqual(result[2]['arrival_time'], '07:28:30')
		self.assertEqual(count, 2)

	def test_intermediate_anchor_and_dwell_are_preserved(self):
		original = [row('07:00:00'), row(), row('07:10:00', '07:12:00'), row(), row('07:30:00')]
		result, _ = interpolate(original, [0, 100, 200, 300, 600])
		for i in (0, 2, 4):
			self.assertEqual(original[i], result[i])
		self.assertEqual(result[3]['arrival_time'], '07:16:30')

	def test_after_midnight_gtfs_hours_are_not_wrapped(self):
		result, _ = interpolate([row('23:50:00'), row(), row('24:20:00')], [0, 1, 2])
		self.assertEqual(result[1]['arrival_time'], '24:05:00')

	def test_missing_endpoint_fails_instead_of_inventing_time(self):
		with self.assertRaises(ValueError):
			interpolate([row(), row('07:30:00')], [0, 100])

	def test_loop_returns_to_same_stop_at_end(self):
		line = LineString([(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)])
		fit = match_line(line, [(0, 0), (100, 0), (0, 100), (0, 0)], 10)
		self.assertEqual(fit['distances'], [0, 100, 300, 400])

	def test_road_path_keeps_detour_in_distance(self):
		line = LineString([(0, 0), (0, 1000), (100, 1000), (100, 0)])
		fit = match_line(line, [(0, 0), (0, 1000), (100, 0)], 10)
		self.assertEqual(fit['distances'][-1], 2100)
		self.assertEqual(fit['distances'][-1], fit['vertex_distances'][-1])

	def test_wrong_stop_sequence_rejects_path(self):
		line = LineString([(0, 0), (1000, 0)])
		self.assertIsNone(match_line(line, [(0, 0), (800, 0), (200, 0), (1000, 0)], 10))

	def test_stop_off_path_rejects_match(self):
		line = LineString([(0, 0), (1000, 0)])
		self.assertIsNone(match_line(line, [(0, 0), (500, 200), (1000, 0)], 100))

	def test_reverse_digitized_line_is_oriented_to_stops(self):
		line = LineString([(1000, 0), (0, 0)])
		self.assertEqual(match_line(line, [(0, 0), (400, 0), (1000, 0)], 10)['distances'], [0, 400, 1000])

	def test_source_parts_keep_repeated_road_traversal(self):
		parts = MultiLineString([[(0, 0), (100, 0)], [(100, 0), (200, 0)],
			[(200, 0), (100, 0)], [(100, 0), (0, 0)]])
		line = connected_source_line(parts)
		self.assertEqual(list(line.coords), [(0, 0), (100, 0), (200, 0), (100, 0), (0, 0)])
		self.assertEqual(line.length, sum(part.length for part in parts.geoms))
		fit = match_line(line, [(0, 0), (200, 0), (0, 0)], 1)
		self.assertEqual(fit['distances'], [0, 200, 400])

	def test_disconnected_parts_are_not_bridged(self):
		parts = MultiLineString([[(0, 0), (100, 0)], [(101, 0), (200, 0)]])
		self.assertIsNone(connected_source_line(parts))

	def test_unordered_but_connected_parts_can_still_merge(self):
		parts = MultiLineString([[(100, 0), (200, 0)], [(0, 0), (100, 0)]])
		line = connected_source_line(parts)
		self.assertEqual(line.length, 200)
		self.assertEqual(match_line(line, [(0, 0), (100, 0), (200, 0)], 1)['distances'], [0, 100, 200])

	def test_unordered_branch_is_not_invented_as_a_traversal(self):
		parts = MultiLineString([[(0, 0), (100, 0)], [(100, 0), (200, 0)], [(100, 0), (100, 100)]])
		self.assertIsNone(connected_source_line(parts))

	def test_source_gap_smaller_than_one_metre_is_still_a_gap(self):
		parts = MultiLineString([[(0, 0), (100, 0)], [(100.000001, 0), (200, 0)]])
		self.assertIsNone(connected_source_line(parts))


if __name__ == '__main__':
	unittest.main()
