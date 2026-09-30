import csv
import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'route_checker'))
from transit_identity import enrich_leg, PALETTES


class TransitIdentityTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.root = Path(self.temp.name)
		path = self.root / 'data/generated/hk-transit-EXPERIMENTAL.gtfs.zip'
		path.parent.mkdir(parents=True)
		with zipfile.ZipFile(path, 'w') as z:
			z.writestr('agency.txt', 'agency_id,agency_name\nKMB,Kowloon Motor Bus\nCTB,Citybus\nGMB,Green Minibus\nXYZ,Other Bus\n')
			z.writestr('routes.txt', 'route_id,agency_id,route_type\n1,KMB,3\n2,CTB,3\n3,GMB,3\n4,KMB+CTB,3\n5,XYZ,3\n6,LWB,3\n')

	def leg(self, identifier, mode='BUS'):
		leg = {'mode': mode, 'route': {'gtfsId': identifier, 'shortName': '10'}}
		enrich_leg(leg, self.root)
		return leg

	def test_operators_and_minibus_keep_otp_bus_mode(self):
		for route, operator, kind in [('1', 'KMB', 'bus'), ('2', 'Citybus', 'bus'), ('3', 'Green minibus', 'minibus'), ('6', 'Long Win', 'bus')]:
			leg = self.leg('feed:' + route)
			self.assertEqual(leg['mode'], 'BUS')
			self.assertEqual(leg['route']['operatorName'], operator)
			self.assertEqual(leg['route']['serviceKind'], kind)
		self.assertNotEqual(self.leg('1')['route']['displayColor'], self.leg('2')['route']['displayColor'])

	def test_joint_and_unknown_are_not_misidentified(self):
		joint = self.leg('feed:4')['route']
		self.assertEqual(joint['operatorIds'], ['KMB', 'CTB'])
		self.assertEqual(joint['operatorName'], 'KMB / Citybus')
		self.assertEqual(self.leg('5')['route']['operatorName'], 'Other Bus')
		self.assertNotIn('serviceKind', self.leg('missing')['route'])

	def test_walking_and_rail_unchanged(self):
		for mode in ['WALK', 'SUBWAY', 'RAIL', 'TRAM']:
			self.assertEqual(self.leg('1', mode), {'mode': mode, 'route': {'gtfsId': '1', 'shortName': '10'}})

	def test_badge_text_contrast(self):
		def luminance(value):
			parts = [int(value[n:n+2], 16) / 255 for n in (0, 2, 4)]
			linear = [v / 12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in parts]
			return sum(x*y for x,y in zip(linear, (.2126,.7152,.0722)))
		for name, (_, background, text) in PALETTES.items():
			self.assertGreaterEqual((luminance(background)+.05)/(luminance(text)+.05), 4.5, name)


if __name__ == '__main__':
	unittest.main()
