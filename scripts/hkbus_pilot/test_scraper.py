import unittest
from bs4 import BeautifulSoup
from scraper import table_grid, extract, directory_entries


def record(html):
	return {
		'fetched_at': '2026-09-18T00:00:00Z',
		'response': {
			'parse': {'title': '測試線', 'pageid': 1, 'revid': 2, 'text': {'*': html}}
		},
	}


class ParserTests(unittest.TestCase):
	def test_span_context(self):
		t = BeautifulSoup(
			'<table><tr><td rowspan="2">③</td><td>07:05</td></tr><tr><td>07:35</td></tr></table>',
			'html.parser',
		).table
		grid = table_grid(t)
		self.assertEqual(grid[1][0]['text'], '③')
		self.assertEqual(grid[1][0]['origin'], [0, 0])
		self.assertEqual(grid[1][1]['text'], '07:35')

	def test_nested_tables_not_duplicated(self):
		result = extract(
			record(
				'<h2>服務時間及班次</h2><table><tr><td><table><tr><td>23:15 - 00:30</td><td>6／7</td></tr></table></td></tr></table>'
			)
		)
		self.assertEqual(len(result['tables']), 1)
		self.assertEqual(len(result['timetable_candidates']), 1)
		self.assertEqual(
			result['timetable_candidates'][0]['cells'], ['23:15 - 00:30', '6／7']
		)
		self.assertEqual(result['timing_export_status'], 'NOT_VALIDATED_FOR_GTFS')

	def test_history_time_is_not_timetable(self):
		result = extract(
			record(
				'<h2>歷史</h2><table><tr><td>07:05</td></tr></table><h2>服務時間</h2><p>客滿即開</p><h2>收費</h2><table><tr><td>$8</td></tr></table>'
			)
		)
		self.assertEqual(result['timetable_candidates'], [])
		self.assertIn('客滿即開', result['sections'][1]['text'])
		self.assertEqual(len(result['tables']), 2)

	def test_directory_route_column_only(self):
		html = '<table><tr><th>路線</th><th>起點</th><th>終點</th></tr><tr><td><a href="/wiki/九巴98D線">98D</a></td><td><a href="/wiki/其他線">Place</a></td><td>終點</td></tr></table>'
		self.assertEqual(
			[e['title'] for e in directory_entries(record(html))], ['九巴98D線']
		)

	def test_hour_minute_layout(self):
		result = extract(
			record(
				'<h2>服務時間</h2><table><tr><th>小時</th><th>分鐘</th></tr><tr><td>10 - 20</td><td>55</td></tr></table>'
			)
		)
		self.assertEqual(result['timetable_candidates'][0]['layout'], 'hour_minute')
		self.assertEqual(result['timetable_candidates'][0]['cells'], ['10 - 20', '55'])

	def test_special_departure_subsection(self):
		result = extract(
			record(
				'<h2>服務時間</h2><h3>特別班次</h3><table><tr><td>07:05、07:35</td></tr></table><h2>行車路線</h2><table><tr><td>18:30</td></tr></table>'
			)
		)
		self.assertEqual(len(result['timetable_candidates']), 1)
		self.assertEqual(
			result['timetable_candidates'][0]['section_path'], ['服務時間', '特別班次']
		)


if __name__ == '__main__':
	unittest.main()
