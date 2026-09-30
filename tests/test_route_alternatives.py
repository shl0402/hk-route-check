import copy
from datetime import datetime, timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'route_checker'))
import server


def node(route, duration, mode='BUS', walk=100, transfers=0):
	return dict(duration=duration,walkDistance=walk,transfers=transfers,end=(datetime.fromisoformat('2026-09-26T08:00:00+08:00')+timedelta(seconds=duration)).isoformat(),
				legs=[dict(mode=mode,transitLeg=True,route={'gtfsId':route},
						**{'from':{'lat':22.3,'lon':114.1},'to':{'lat':22.4,'lon':114.2}})])

class AlternativeTests(unittest.TestCase):
	def test_duration_patterns_keep_whole_mtr_journey_guard(self):
		rail = dict(transitLeg=True,route=dict(gtfsId='1:RAIL:MTR:TKL:PATH:2220'))
		walk = dict(transitLeg=False)
		bus = dict(transitLeg=True,route=dict(gtfsId='1:BUS:790'))
		self.assertTrue(server.has_split_mtr_journey(dict(legs=[rail,walk,rail])))
		self.assertFalse(server.has_split_mtr_journey(dict(legs=[walk,rail,walk])))
		self.assertFalse(server.has_split_mtr_journey(dict(legs=[rail,bus,rail])))

	def run_search(self, extra=None, **kwargs):
		data=dict(origin={'lat':22.3,'lon':114.1},destination={'lat':22.4,'lon':114.2},modes=['mtr','bus'],preference='fastest')
		data.update(extra or {})
		calls=[]
		def query(q,v,**options):
			calls.append(copy.deepcopy(v))
			modes={x['mode'] for x in v['modes']['transit']['transit']}
			edges=[node('RAIL:MTR:TKL',3000,'SUBWAY')]
			if modes=={'BUS','COACH'}:
				edges=[node('BUS:790',3600,walk=30,transfers=0),node('BUS:790',3900,walk=30),node('BUS:971',4000,walk=20)]
			return {'planConnection':dict(edges=[{'node':x} for x in edges],routingErrors=[])}
		with patch.object(server,'validate_request',return_value=datetime.fromisoformat('2026-09-26T07:32:00+08:00')),patch.object(server,'graphql',side_effect=query),patch.object(server,'normalize',side_effect=lambda x,*args:x),patch.object(server,'route_sources',return_value={}),patch.object(server,'separate_tram_filters',return_value=None),patch.object(server,'recover_mtr_candidates',return_value=([],{'queries':0})):
			result=server.plan(data,**kwargs)
		return result,calls

	def test_extra_modes_are_returned_deduplicated_and_ranked(self):
		result,calls=self.run_search()
		self.assertEqual([x['duration'] for x in result['itineraries']],[3000,3600,4000])
		self.assertEqual(len(calls),3)
		self.assertEqual({m['mode'] for m in calls[-1]['modes']['transit']['transit']},{'BUS','COACH'})
		self.assertEqual(result['maxResults'],6)

	def test_toggle_and_worker_replay_skip_additional_searches(self):
		for extra,kw in [({'includeAlternatives':False},{}),({'maxResults':1},{}),({}, {'for_optimization':'fast'})]:
			result,calls=self.run_search(extra,**kw)
			self.assertEqual(len(calls),2)
			self.assertEqual(len(result['itineraries']),1)

	def test_preference_still_ranks_all_routes(self):
		result,_=self.run_search({'preference':'walking'})
		self.assertEqual([x['walkDistance'] for x in result['itineraries']],[20,30,100])

	def test_unselected_transport_is_not_added(self):
		result,calls=self.run_search({'modes':['mtr']})
		self.assertEqual(len(calls),1)
		self.assertEqual({m['mode'] for m in calls[0]['modes']['transit']['transit']},{'SUBWAY','RAIL'})

	def test_invalid_options(self):
		for value in [0,11,True,'6']:
			with self.assertRaises(ValueError):self.run_search({'maxResults':value})
		with self.assertRaises(ValueError):self.run_search({'includeAlternatives':'true'})

	def test_long_walk_does_not_hide_a_sparse_transit_departure(self):
		walk=node('',10800,mode='WALK');walk['legs'][0]['transitLeg']=False
		ferry=node('FERRY:1',8000,mode='FERRY')
		responses=[{'planConnection':dict(edges=[{'node':n}],routingErrors=[])} for n in (walk,ferry)]
		with patch.object(server,'validate_request',return_value=datetime.fromisoformat('2026-09-26T08:00:00+08:00')),patch.object(server,'normalize',side_effect=lambda n,*_:n),patch.object(server,'route_sources',return_value={}),patch.object(server,'graphql',side_effect=responses) as call:
			result=server.plan(dict(origin={'lat':22.3,'lon':114.1},destination={'lat':22.4,'lon':114.2},modes=['ferry'],preference='fastest'))
		self.assertEqual(result['itineraries'][0]['legs'][0]['mode'],'FERRY')
		self.assertIn('PT2H',call.call_args.args[0])

	def test_failed_alternative_keeps_main_result(self):
		original=server.graphql
		with patch.object(server,'validate_request',return_value=datetime.fromisoformat('2026-09-26T07:32:00+08:00')),patch.object(server,'normalize',side_effect=lambda x,*args:x),patch.object(server,'route_sources',return_value={}),patch.object(server,'separate_tram_filters',return_value=None),patch.object(server,'recover_mtr_candidates',return_value=([],{'queries':0})):
			response={'planConnection':dict(edges=[{'node':node('RAIL:MTR:TKL',3000,'SUBWAY')}],routingErrors=[])}
			with patch.object(server,'graphql',side_effect=[copy.deepcopy(response),copy.deepcopy(response),TimeoutError()]):
				result=server.plan(dict(origin={'lat':22.3,'lon':114.1},destination={'lat':22.4,'lon':114.2},modes=['mtr','bus'],preference='fastest'))
			self.assertEqual(len(result['itineraries']),1)
			self.assertEqual(len(result['warnings']),1)

	def test_failed_later_departure_search_keeps_walk(self):
		walk=node('',10800,mode='WALK');walk['legs'][0]['transitLeg']=False
		response={'planConnection':dict(edges=[{'node':walk}],routingErrors=[])}
		with patch.object(server,'validate_request',return_value=datetime.fromisoformat('2026-09-26T08:00:00+08:00')),patch.object(server,'normalize',side_effect=lambda n,*_:n),patch.object(server,'route_sources',return_value={}),patch.object(server,'graphql',side_effect=[response,TimeoutError()]):
			result=server.plan(dict(origin={'lat':22.3,'lon':114.1},destination={'lat':22.4,'lon':114.2},modes=['ferry'],preference='fastest'))
		self.assertEqual(result['itineraries'],[walk])
		self.assertEqual(len(result['warnings']),1)

if __name__=='__main__':unittest.main()
