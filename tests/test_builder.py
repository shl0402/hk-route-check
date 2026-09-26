import unittest, sys, json, zipfile, csv, io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_gtfs as b


class BuilderTests(unittest.TestCase):
	def test_namespaces_keep_different_places(self):
		rows = [r for rr in b.network().values() for r in rr]
		names = {r['sid']: r['English Name'] for r in rows}
		self.assertEqual(names['RAIL:MTR:100'], 'Tai Shui Hang')
		self.assertEqual(names['RAIL:LRT:100'], 'Siu Hong')
		self.assertEqual(names['RAIL:MTR:120'], 'Tuen Mun')
		self.assertEqual(names['RAIL:LRT:120'], 'Ching Chung')

	def test_clock_after_midnight(self):
		self.assertEqual(b.clock(25 * 3600 + 60), '25:01:00')
		self.assertEqual(b.seconds('25:01:00'), 90060)

	def test_no_placeholders_and_missing_station_recovered(self):
		report = {'coordinate_disagreements': []}
		coords = b.geography(b.network(), report)
		self.assertIn('RAIL:LRT:120', coords)
		self.assertNotIn((22.3, 114.1), coords.values())

	def test_frequency_not_fake_one_minute(self):
		table = b.published_frequencies()
		h, proof = b.frequency_for(('MTR', 'ISL', 'UT'), table)
		self.assertGreater(h, 60)
		self.assertEqual(proof['label'], 'Island Line')

	def test_csv_quoting(self):
		text = b.write_table([{'name': 'Station, "A"'}], ['name'])
		self.assertEqual(
			list(csv.DictReader(io.StringIO(text)))[0]['name'], 'Station, "A"'
		)

	def test_no_legacy_duration_passed_off_as_running_time(self):
		report = json.loads((ROOT / 'data/generated/quality_report.json').read_text())
		for p in report['patterns'].values():
			for segment in p['segments']:
				if 'legacy_value_used' in segment['evidence']:
					self.assertFalse(segment['evidence']['legacy_value_used'])

	def test_built_file_has_no_id_collision(self):
		with zipfile.ZipFile(
			ROOT / 'data/generated/hk-transit-EXPERIMENTAL.gtfs.zip'
		) as z:
			rows = list(csv.DictReader(io.StringIO(z.read('stops.txt').decode())))
		ids = [r['stop_id'] for r in rows]
		self.assertEqual(len(ids), len(set(ids)))


if __name__ == '__main__':
	unittest.main()
