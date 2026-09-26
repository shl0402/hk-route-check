import unittest
from normalize_mtr_api import hr_observations, lr_routes


class ParsingTests(unittest.TestCase):
	def node(self, id, time, line=15, kind='RIDE'):
		return dict(ID=id, time=time, lineID=line, linkType=kind)

	def test_excludes_initial_boarding(self):
		body = {
			'routes': [
				{
					'path': [
						self.node(1, 0),
						self.node(2, 4),
						self.node(3, 6, kind='END'),
					]
				}
			]
		}
		self.assertEqual(
			list(hr_observations(body, {'15': 'TKL'})), [('MTR:TKL:2>3', 120)]
		)

	def test_excludes_interchange(self):
		body = {
			'routes': [
				{
					'path': [
						self.node(1, 0),
						self.node(2, 4, 13, 'INTERCHANGE'),
						self.node(3, 6, 13),
						self.node(4, 8, 13, 'END'),
					]
				}
			]
		}
		self.assertEqual(list(hr_observations(body, {'15': 'TKL', '13': 'ISL'})), [])

	def test_null_is_not_zero(self):
		body = {
			'routes': [
				{
					'path': [
						self.node(1, 0),
						self.node(2, None),
						self.node(3, 6, kind='END'),
					]
				}
			]
		}
		self.assertEqual(list(hr_observations(body, {'15': 'TKL'})), [])

	def test_conditional_paths_excluded(self):
		body = {
			'routes': [
				{
					'messages': [{'msgText': 'Special arrangement'}],
					'path': [
						self.node(1, 0),
						self.node(2, 4),
						self.node(3, 6, kind='END'),
					],
				}
			]
		}
		self.assertEqual(list(hr_observations(body, {'15': 'TKL'})), [])

	def test_lrt_keeps_line_alternatives_without_inventing_intermediate_times(self):
		path = [
			dict(
				ID='020',
				time='0',
				linkType='RIDE',
				step=[{'lineID': '610'}, {'lineID': '615'}],
			),
			dict(ID='030', time=None, linkType='RIDE'),
			dict(ID='040', time='7', linkType='END'),
		]
		self.assertEqual(
			list(lr_routes({'routes': [dict(path=path, time='7')]})),
			[(('20', '30', '40'), {'610', '615'}, 7.0)],
		)

	def test_lrt_transfer_rejected(self):
		path = [
			dict(ID='20', linkType='RIDE', step=[{'lineID': '610'}]),
			dict(ID='30', linkType='INTERCHANGE'),
			dict(ID='40', linkType='END'),
		]
		self.assertEqual(list(lr_routes({'routes': [dict(path=path, time='7')]})), [])


if __name__ == '__main__':
	unittest.main()
