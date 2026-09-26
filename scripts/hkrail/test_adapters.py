import unittest, json
from normalize import ROOT, normalize, interval
from line_adapters import compile_article
from merge_gtfs import safe_rows, sec

P = ROOT / 'data/rail_wiki'


def article(code):
	return json.loads((P / 'articles' / f'{code}.json').read_text())


def groups(code):
	a = article(code)
	return compile_article(a, normalize(a))


class ReviewedLayouts(unittest.TestCase):
	def test_unicode_range(self):
		self.assertEqual(interval('11–13')['max_seconds'], 780)

	def test_short_turn_colour_stops_at_uncoloured_row(self):
		gs = groups('TCL')
		g = next(g for g in gs if g['table_index'] == 2 and g['days'] == list(range(5)))
		self.assertTrue(
			next(r for r in g['records'] if r.get('start_seconds') == sec('07:26:00'))[
				'mixed'
			]
		)
		self.assertFalse(
			next(
				r for r in g['records'] if r.get('start_seconds') == sec('09:24:00')
			).get('mixed', False)
		)

	def test_late_kwun_tong_trains_not_full_length(self):
		g = next(g for g in groups('KTL') if g['origin'] == '黃埔')
		r = next(
			r for r in g['records'] if sec('25:00:00') in r.get('departure_seconds', [])
		)
		self.assertEqual(r['destination'], '觀塘')
		self.assertFalse(r['mixed'])

	def test_twl_conflict_only_rejects_affected_rows(self):
		g = next(g for g in groups('TWL') if g['origin'] == '中環' and g['days'] == [4])
		accepted, windows, rejected = safe_rows(g['records'])
		self.assertTrue(rejected)
		self.assertTrue(accepted)
		self.assertTrue(any(a <= sec('20:16:00') < b for a, b in windows))
		self.assertTrue(
			any(r.get('start_seconds') == sec('16:45:00') for r in accepted)
		)

	def test_lohas_colour_maps_each_departure(self):
		a = json.loads((P / 'supplements/TKL_LOHAS.json').read_text())
		gs = compile_article(a, normalize(a))
		weekday = gs[0]
		sunday = gs[2]
		self.assertEqual(
			next(
				r
				for r in weekday['records']
				if r['departure_seconds'] == [sec('07:03:00')]
			)['destination'],
			'北角',
		)
		self.assertTrue(all(r['destination'] == '調景嶺' for r in sunday['records']))
		self.assertTrue(
			any(r['departure_seconds'] == [sec('15:02:00')] for r in gs[1]['records'])
		)

	def test_tkl_branch_uses_origin_column_only(self):
		g = next(g for g in groups('TKL') if g.get('replace_branch') == 'TKS-UT')
		r = next(
			r for r in g['records'] if r.get('departure_seconds') == [sec('07:07:00')]
		)
		self.assertEqual(r['origin'], '北角')
		self.assertFalse(
			any(sec('07:16:00') in r.get('departure_seconds', []) for r in g['records'])
		)

	def test_disney_does_not_assume_open_calendar(self):
		for g in groups('DRL'):
			self.assertTrue(
				all(
					r['kind'] == 'frequency'
					and r['interval']['kind'] == 'conditional_envelope'
					for r in g['records']
				)
			)

	def test_east_rail_uses_branch_not_trunk_interval(self):
		g = next(
			g
			for g in groups('EAL')
			if g['replace_branch'] == 'UT' and g['days'] == list(range(5))
		)
		r = next(r for r in g['records'] if r['start_seconds'] == sec('10:00:00'))
		self.assertEqual(r['interval']['max_seconds'], 480)  # trunk is 4, branch is 4/8

	def test_east_bold_cells_only_and_late_short_trains(self):
		gs = [g for g in groups('EAL') if g['special']]
		self.assertEqual(len(gs), 8)
		self.assertTrue(
			any(g['origin'] == '旺角東' and g['destination'] == '落馬洲' for g in gs)
		)
		self.assertTrue(
			any(g['origin'] == '羅湖' and g['destination'] == '紅磡' for g in gs)
		)
		self.assertFalse(any(g['origin'] == '粉嶺' for g in gs))

	def test_light_rail_own_tables_only(self):
		for c in ['614', '614P', '615', '615P']:
			self.assertEqual({g['table_index'] for g in groups(c)}, {0, 1})

	def test_unknown_school_days_not_daily(self):
		for c in ['506P', '610P', '720']:
			self.assertEqual(groups(c), [])

	def test_751p_conflicting_main_not_merged(self):
		self.assertEqual({g['table_index'] for g in groups('751P')}, {5})


if __name__ == '__main__':
	unittest.main(verbosity=2)
