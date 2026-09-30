"""Regression for the LOHAS Park → Tsim Sha Tsui multi-line OD connection."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'route_checker'))
from mtr_sections import rail_sections
import server


class MTRSectionsTests(unittest.TestCase):
	def setUp(self):
		self.sample = json.loads((ROOT / 'tests/fixtures/mtr_interchange_journey.json').read_text())
		self.leg, self.journey = self.sample['leg'], self.sample['journey']

	def test_each_change_including_same_line_gets_a_section(self):
		parts = rail_sections(self.leg, self.journey)
		self.assertEqual([p['route']['shortName'] for p in parts], ['TKL', 'TKL', 'ISL', 'TWL'])
		self.assertEqual([len(p['stopCalls']) - 1 for p in parts], [1, 4, 5, 1])
		self.assertEqual(sum(p['duration'] for p in parts), self.leg['duration'])
		self.assertEqual([p['waitBeforeSeconds'] for p in parts], [60, 0, 0, 0])
		for left, right in zip(parts, parts[1:]):
			self.assertEqual(left['to'], right['from'])
			self.assertEqual(left['end'], right['start'])
			self.assertEqual(left['apiPath'][-1], right['apiPath'][0])
		self.assertEqual(parts[0]['start'], self.leg['start'])
		self.assertEqual(parts[-1]['end'], self.leg['end'])
		self.assertTrue(all(p['internalTransfers'] == 0 for p in parts))

	def test_does_not_reuse_whole_trip_geometry_or_colour(self):
		self.leg['legGeometry'] = {'points': 'whole-route'}
		self.leg['route']['displayColor'] = 'purple'
		original = copy.deepcopy(self.leg)
		parts = rail_sections(self.leg, self.journey)
		self.assertEqual(self.leg, original)
		for p in parts:
			self.assertIsNone(p['legGeometry'])
			self.assertNotEqual(p['route']['displayColor'], 'purple')
			self.assertEqual(len(p['apiPath']), len(p['stopCalls']))
		self.assertEqual([p['route']['displayColor'] for p in parts], ['6B208B', '6B208B', '0860A8', 'FF0000'])

	def test_no_split_on_single_line_or_missing_evidence(self):
		self.journey['interchanges'] = []
		self.journey['stop_ids'] = [s.rsplit(':', 1)[0] + ':TKL' for s in self.journey['stop_ids']]
		self.assertEqual(rail_sections(self.leg, self.journey), [])

	def test_response_keeps_one_mtr_connection_and_exposes_sections(self):
		self.leg['trip'] = {'gtfsId': '1:test-mtr-trip'}
		context = {
			'odtrips': {'test-mtr-trip': {'journey': 'test', 'boarding_offset_seconds': 0}},
			'odjourneys': {'test': self.journey},
			'feedstops': {
				c['stopLocation']['gtfsId']: {
					'stop_name': c['stopLocation']['name'],
					'stop_lat': c['stopLocation']['lat'], 'stop_lon': c['stopLocation']['lon']}
				for c in self.leg['stopCalls']
			},
		}
		itinerary = {'legs': [self.leg], 'duration': self.leg['duration'],
			'start': self.leg['start']['scheduledTime'], 'end': self.leg['end']['scheduledTime']}
		with patch.object(server, 'source_context', return_value=context), \
			patch.object(server, 'leg_provenance', return_value={}):
			result = server.normalize(itinerary)
		self.assertEqual(len(result['legs']), 1)
		self.assertEqual(result['boardings'], 1)
		self.assertEqual(result['transfers'], 0)
		self.assertEqual(len(result['legs'][0]['railSections']), 4)
		self.assertEqual(result['rideSeconds'], self.journey['api_total_seconds'])
		self.leg['stopCalls'] = []
		self.assertEqual(rail_sections(self.leg, self.journey), [])


if __name__ == '__main__':
	unittest.main()
