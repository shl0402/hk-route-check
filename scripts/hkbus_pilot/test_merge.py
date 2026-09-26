import unittest
from bs4 import BeautifulSoup
from datetime import date
from merge_gtfs import parse_table, table_grid, dates_for, day_rule


def table(html):
	return {'rows': table_grid(BeautifulSoup(html, 'html.parser').table), 'index': 0}


class MergeTests(unittest.TestCase):
	def test_hour_table_rollover_and_range(self):
		g, _ = parse_table(
			table(
				'<table><tr><th colspan="2">由甲開出時間</th></tr><tr><th>小時</th><th>分鐘</th></tr><tr><td colspan="2">每日</td></tr><tr><td>22 - 23</td><td>00、30</td></tr><tr><td>00</td><td>15</td></tr></table>'
			)
		)
		self.assertEqual(g[0]['departures'], [79200, 81000, 82800, 84600, 87300])
		self.assertFalse(g[0]['errors'])

	def test_shared_headway_two_directions(self):
		g, _ = parse_table(
			table(
				'<table><tr><th>由甲開出</th><th>由乙開出</th><th>班次（分鐘）</th></tr><tr><td colspan="3">星期一至六</td></tr><tr><td>06:00 - 20:00</td><td>06:30 - 20:30</td><td>60</td></tr></table>'
			)
		)
		self.assertEqual(len(g), 2)
		self.assertEqual(g[1]['periods'][0]['start'], 23400)
		self.assertEqual(g[0]['days'], list(range(6)))

	def test_variable_frequency_is_not_made_exact(self):
		g, _ = parse_table(
			table(
				'<table><tr><th colspan="2">由甲開出</th></tr><tr><td colspan="2">每日</td></tr><tr><td>06:00 - 20:00</td><td>6／7</td></tr></table>'
			)
		)
		self.assertTrue(g[0]['errors'])
		self.assertFalse(g[0]['periods'])

	def test_merged_departure_is_not_duplicated(self):
		g, _ = parse_table(
			table(
				'<table><tr><th colspan="2">由甲開出</th></tr><tr><td colspan="2">每日</td></tr><tr><td colspan="2">07:05</td></tr></table>'
			)
		)
		self.assertEqual(g[0]['departures'], [25500])

	def test_unknown_school_calendar_is_not_daily(self):
		self.assertIsNone(day_rule('星期一至五上課日'))

	def test_overnight_period(self):
		g, _ = parse_table(
			table(
				'<table><tr><th colspan="2">由甲開出</th></tr><tr><td colspan="2">每日</td></tr><tr><td>23:15 - 00:30</td><td>25</td></tr></table>'
			)
		)
		self.assertEqual(g[0]['periods'][0]['end'], 88200)

	def test_holiday_replaces_weekday_category(self):
		self.assertEqual(
			dates_for([3], date(2026, 10, 1), date(2026, 10, 2), {'20261001'}), []
		)
		self.assertEqual(
			dates_for([7], date(2026, 10, 1), date(2026, 10, 2), {'20261001'}),
			['20261001'],
		)

	def test_footnote_cannot_be_silently_dropped(self):
		g, _ = parse_table(
			table(
				'<table><tr><th colspan="2">由甲開出時間</th></tr><tr><th>小時</th><th>分鐘</th></tr><tr><td colspan="2">每日</td></tr><tr><td>06</td><td>15＊、40＊</td></tr></table>'
			)
		)
		self.assertTrue(g[0]['errors'])


if __name__ == '__main__':
	unittest.main()
