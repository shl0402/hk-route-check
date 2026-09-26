import argparse, contextlib, io, json, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
import bulk
from scraper import save


class BulkTests(unittest.TestCase):
	def test_selection_keeps_uncertain_public_routes(self):
		self.assertTrue(
			bulk.choose({'wiki_title': '九巴12P線', 'gtfs_candidates': []}, [])[0]
		)
		self.assertTrue(
			bulk.choose(
				{'wiki_title': '公共小巴中環至香港仔線', 'gtfs_candidates': []}, []
			)[0]
		)
		self.assertFalse(
			bulk.choose({'wiki_title': '居民巴士NR329線', 'gtfs_candidates': []}, [])[0]
		)
		self.assertTrue(
			bulk.choose({'wiki_title': '居民巴士NR329線', 'gtfs_candidates': [{}]}, [])[
				0
			]
		)

	def test_resume_skips_completed_and_recovers_interrupted(self):
		with tempfile.TemporaryDirectory() as td:
			out = Path(td)
			gtfs = out / 'gtfs.zip'
			with zipfile.ZipFile(gtfs, 'w') as z:
				z.writestr(
					'routes.txt',
					'route_id,agency_id,route_short_name,route_long_name\n1,KMB,10,A - B\n2,KMB,11,A - C\n',
				)
			save(
				out / 'inventory.json',
				{
					'entries': [
						{'title': t, 'directory': 'test', 'status_hint': 'unverified'}
						for t in ['九巴10線', '九巴11線']
					]
				},
			)
			args = argparse.Namespace(
				output=str(out),
				gtfs=str(gtfs),
				status=False,
				prepare=False,
				retry_failed=False,
				refresh=False,
				offline=False,
				delay=1,
				limit=1,
			)
			calls = []

			class FakeClient:
				def __init__(self, *a, **k):
					pass

				def page(self, title):
					calls.append(title)
					r = {
						'fetched_at': 'test',
						'response': {
							'parse': {
								'title': title,
								'pageid': 10 if '10' in title else 11,
								'revid': 1,
								'text': {'*': '<h2>服務時間</h2><p>07:00</p>'},
							}
						},
					}
					save(bulk.raw_path(out, title), r)
					return r

			with patch.object(bulk, 'Client', FakeClient), contextlib.redirect_stdout(
				io.StringIO()
			):
				self.assertEqual(bulk.run(args), 2)
				db = bulk.connect(out / 'bulk.sqlite')
				db.execute("UPDATE jobs SET state='running' WHERE state='pending'")
				db.commit()
				db.close()
				self.assertEqual(bulk.run(args), 0)
				self.assertEqual(bulk.run(args), 0)
			self.assertEqual(calls, ['九巴10線', '九巴11線'])
			self.assertEqual(
				json.loads((out / 'bulk_status.json').read_text())['done'], 2
			)

	def test_cross_harbour_match(self):
		with tempfile.TemporaryDirectory() as td:
			p = Path(td) / 'g.zip'
			with zipfile.ZipFile(p, 'w') as z:
				z.writestr(
					'routes.txt',
					'route_id,agency_id,route_short_name\n1,KMB+CTB,103\n2,CTB,103\n',
				)
			c = bulk.compare([{'title': '過海隧巴103線'}], p)
			self.assertEqual(len(c['comparisons'][0]['gtfs_candidates']), 2)


if __name__ == '__main__':
	unittest.main()
