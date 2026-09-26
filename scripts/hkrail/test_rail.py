import json, unittest
from pathlib import Path
from normalize import days, interval, timing_row, normalize, OUT


class FormatTests(unittest.TestCase):
	def test_decimal_range_keeps_both_bounds(self):
		r = interval('3.3-7.5')
		self.assertEqual((r['min_seconds'], r['max_seconds']), (198, 450))
		self.assertEqual(r['kind'], 'range')

	def test_alternating_is_not_constant(self):
		self.assertEqual(interval('4/8')['kind'], 'alternating')

	def test_race_day_annotation_is_not_silently_removed(self):
		self.assertEqual(
			interval('3.3 [星期一至四] 3-3.4 [星期五]')['kind'], 'unresolved'
		)

	def test_midnight_is_previous_service_day(self):
		r = timing_row(['23:55-00:35', '9-11'])
		self.assertEqual(r['end_seconds'], 24 * 3600 + 35 * 60)

	def test_departures_do_not_duplicate_merged_cells(self):
		self.assertEqual(
			len(timing_row(['06:00、06:10', '06:00、06:10'])['departure_seconds']), 2
		)

	def test_holiday_is_distinct_from_weekday(self):
		self.assertNotIn(7, days('星期一至五'))
		self.assertIn(7, days('星期日及公眾假期'))
		self.assertIsNone(days('星期一至五上課日'))


class CachedFixtureTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.articles = {
			p.stem: json.loads(p.read_text()) for p in (OUT / 'articles').glob('*.json')
		}

	def test_all_baseline_lines_were_fetched(self):
		inventory = json.loads((OUT / 'inventory.json').read_text())['lines']
		self.assertTrue(all(e['code'] in self.articles for e in inventory))
		self.assertEqual(len(inventory), 29)

	def test_east_rail_is_not_flattened_into_terminal_service(self):
		r = normalize(self.articles['EAL'])
		self.assertEqual(r['terminal_timetables'], [])
		self.assertTrue(
			any(
				'落馬洲' in (x['section_or_direction'] or '')
				for x in r['section_headways']
			)
		)
		self.assertTrue(any('馬場' in s['text'] for s in r['operational_notes']))

	def test_disney_open_closed_calendars_preserved(self):
		r = normalize(self.articles['DRL'])
		self.assertEqual(len(r['hour_minute_timetables']), 4)
		self.assertTrue(
			any(
				'閉園日' in x['calendar_or_condition']
				for x in r['hour_minute_timetables']
			)
		)
		self.assertTrue(all(x['departures'] for x in r['hour_minute_timetables']))

	def test_first_last_caption_classified(self):
		r = normalize(self.articles['SIL'])
		self.assertEqual(
			{v['kind'] for v in r['station_first_last']}, {'first', 'last'}
		)

	def test_short_turns_are_quarantined(self):
		r = normalize(self.articles['TML'])
		self.assertFalse(any(g['merge_candidate'] for g in r['terminal_timetables']))

	def test_span_repair_does_not_change_raw_cache(self):
		a = self.articles['KTL']
		self.assertEqual(len(a['span_repairs']), 1)
		self.assertFalse(a['issues'])

	def test_lohas_markup_and_destination_rules_retained(self):
		a = json.loads((OUT / 'supplements/TKL_LOHAS.json').read_text())
		r = normalize(a)
		self.assertFalse(a['issues'])
		self.assertGreaterEqual(len(a['span_repairs']), 3)
		self.assertTrue(r['hour_minute_timetables'])
		self.assertTrue(
			r['hour_minute_timetables'][0]['departures'][0]['source_cell']['raw_html']
		)


if __name__ == '__main__':
	unittest.main(verbosity=2)
