"""Replay real OTP frequency pruning without a network or rebuilt graph."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'route_checker'))
import server
import route_selection


class DepartureWindowTests(unittest.TestCase):
	def setUp(self):
		self.sample = json.loads((ROOT / 'tests/fixtures/fortress_hill_frequency_wait.json').read_text())
		self.variables = {'date': {'earliestDeparture': '2026-10-02T11:00:00+08:00'},
			'modes': {'transit': {'transit': [{'mode': 'SUBWAY'}]}},
			'prefs': {'transit': {'transfer': {'maximumTransfers': 0}}},
			'via': [{'passThrough': {'stopLocationIds': ['1:RAIL:MTR:30:ISL']}}]}
		self.context = {'frequencies': {
			server.raw_gtfs_id(leg['trip']['gtfsId']): [{'exactTimes': 0, 'headwaySeconds': 246}]
			for edge in self.sample['wide']['edges'] for leg in edge['node']['legs']
			if leg['transitLeg']}}

	def run_search(self, second=None, context=None, query=None):
		responses = [{'planConnection': copy.deepcopy(self.sample['wide'])}]
		responses.append(second if isinstance(second, Exception) else {'planConnection': copy.deepcopy(second or self.sample['early'])})
		warnings = []
		with patch.object(server, 'source_context', return_value=context or self.context), \
			patch.object(server, 'graphql', side_effect=responses) as call:
			result = server.plan_connection(query or server.QUERY, self.variables, warnings)
		return result, warnings, call

	def test_real_earlier_route_is_recovered_without_removing_frequency_wait(self):
		result, warnings, call = self.run_search()
		self.assertFalse(warnings)
		self.assertEqual(call.call_count, 2)
		self.assertIn('searchWindow:"PT1M"', call.call_args.args[0])
		self.assertEqual(call.call_args.args[1], self.variables)
		items = [edge['node'] for edge in result['edges']]
		best = route_selection.select(items, 'fastest', 6, server.timestamp)[0]
		self.assertEqual(best['start'], '2026-10-02T11:00:00+08:00')
		self.assertEqual(best['end'], '2026-10-02T11:35:55+08:00')
		self.assertEqual(best['waitingTime'], 246)
		self.assertEqual(best, self.sample['early']['edges'][0]['node'])
		self.assertEqual(result['edges'][0], self.sample['wide']['edges'][0])

	def test_scheduled_service_does_not_need_frequency_workaround(self):
		context = copy.deepcopy(self.context)
		for rows in context['frequencies'].values():
			rows[0]['exactTimes'] = 1
		result, warnings, call = self.run_search(context=context)
		self.assertEqual(call.call_count, 1)
		self.assertEqual(result, self.sample['wide'])

	def test_empty_departure_minute_keeps_later_service(self):
		result, warnings, _ = self.run_search(second={'edges': [], 'routingErrors': [{'code': 'NO_TRANSIT_CONNECTION'}]})
		self.assertFalse(warnings)
		self.assertEqual(result['edges'], self.sample['wide']['edges'])

	def test_failed_check_preserves_routes_and_reports_incomplete_search(self):
		for error in (TimeoutError(), RuntimeError('OTP unavailable')):
			result, warnings, _ = self.run_search(second=error)
			self.assertEqual(result['edges'], self.sample['wide']['edges'])
			self.assertEqual(len(warnings), 1)

	def test_one_minute_query_is_not_repeated(self):
		_, _, call = self.run_search(query=server.QUERY.replace('PT1H', 'PT1M'))
		self.assertEqual(call.call_count, 1)

	def test_fast_and_station_constrained_queries_retain_all_constraints(self):
		for query in (server.MTR_ALTERNATIVE_QUERY, server.QUERY.replace('first:30,searchWindow:"PT1H"', 'first:6,searchWindow:"PT10M"')):
			_, _, call = self.run_search(query=query)
			self.assertEqual(call.call_args.args[1], self.variables)
			self.assertEqual(call.call_args.args[0], query.replace('PT1H', 'PT1M').replace('PT10M', 'PT1M'))


if __name__ == '__main__':
	unittest.main()
