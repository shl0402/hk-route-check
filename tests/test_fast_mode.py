import csv
import io
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime
import zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'route_checker'))
import optimizer as opt
from r5_worker import group,filtered_feed
from test_optimizer import Router,Cache,request


def seed(spec,emit,cancelled):
    return [dict(fromIndex=i,toIndex=j,seconds=0 if i==j else 300) for i in range(1,len(spec['points'])) for j in range(1,len(spec['points']))],dict(r5CacheHit=False)


class FastModeTests(unittest.TestCase):
    def test_default_and_invalid_mode(self):
        self.assertEqual(opt.validate(request(),Router)['planningMode'],'full')
        with self.assertRaises(ValueError):opt.validate(dict(request(),planningMode='unknown'),Router)

    def test_otp_only_checks_selected_edges(self):
        class Counting(Cache):
            def __init__(self):super().__init__();self.calls=[]
            def route(self,a,b,departure,modes):
                self.calls.append((a['id'],b['id']));return super().route(a,b,departure,modes)
        spec=opt.validate(dict(request(4),planningMode='fast'),Router);cache=Counting()
        result=opt.run(spec,cache,lambda *a,**kw:None,lambda:False,seed)
        self.assertTrue(result['feasible']);self.assertEqual(result['planningMode'],'fast')
        self.assertEqual(len(cache.calls),3)  # 4 jobs, 1 worker, free endpoints; not 12 matrix calls.
        self.assertEqual(result['metrics']['otpRequests'],3)

    def test_seed_underestimate_is_repaired_and_checked(self):
        result=opt.run(opt.validate(dict(request(3),planningMode='fast'),Router),Cache(600),lambda *a,**kw:None,lambda:False,seed)
        self.assertTrue(result['feasible']);self.assertEqual(result['metrics']['rounds'],2)

    def test_available_appointment_slack_avoids_unnecessary_repair(self):
        data=request(2);data['planningMode']='fast'
        data['jobs'][0]['window']={'start':'2026-09-24T09:00','end':'2026-09-24T09:20'}
        data['jobs'][1]['window']={'start':'2026-09-24T10:00','end':'2026-09-24T10:20'}
        result=opt.run(opt.validate(data,Router),Cache(600),lambda *a,**kw:None,lambda:False,seed)
        self.assertTrue(result['feasible']);self.assertEqual(result['metrics']['rounds'],1)

    def test_fast_mode_checks_bases_job_windows_and_both_break_choices(self):
        for latest in ('2026-09-24T09:50','2026-09-24T11:00'):
            data=request(4);data['planningMode']='fast'
            data['break']={'minutes':30,'earliest':'2026-09-24T09:20','latest':latest}
            data['workers']=[dict(id='named-worker',startLocation={'lat':22.31,'lon':114.1},endLocation={'lat':22.32,'lon':114.1})]
            result=opt.run(opt.validate(data,Router),Cache(600),lambda *a,**kw:None,lambda:False,seed)
            self.assertTrue(result['feasible'])
            self.assertEqual(result['schedules'][0]['worker'],'named-worker')
            events=result['schedules'][0]['events']
            self.assertEqual(sum(e['kind']=='travel' for e in events),5)
            pause=next(e for e in events if e['kind']=='break')
            for e in events:
                if e['kind'] in ('travel','job'):
                    self.assertTrue(e['end']<=pause['start'] or e['start']>=pause['end'])

    def test_seed_is_never_reported_as_checked_on_otp_failure(self):
        class Slow(Cache):
            def route(self,*args):raise TimeoutError()
        with self.assertRaises(opt.RoutingTimeout):
            opt.run(opt.validate(dict(request(2),planningMode='fast'),Router),Slow(),lambda *a,**kw:None,lambda:False,seed)

    def test_fast_replay_preserves_whole_mtr_search(self):
        import server
        data=dict(origin={'lat':22.3,'lon':114.1},destination={'lat':22.4,'lon':114.2},modes=['mtr','bus'],preference='fastest')
        node=dict(end='2026-09-24T10:00:00+08:00',walkDistance=10,legs=[])
        def response(*args, **kwargs):return {'planConnection':{'edges':[{'node':node}], 'routingErrors':[]}}
        with patch.object(server,'validate_request',return_value=datetime.fromisoformat('2026-09-24T09:00:00+08:00')), patch.object(server,'graphql',side_effect=response) as query, patch.object(server,'normalize',side_effect=lambda x,*_:x), patch.object(server,'has_split_mtr_journey',return_value=False), patch.object(server,'route_sources',return_value={}):
            result=server.plan(data,for_optimization='fast')
            self.assertEqual(len(result['itineraries']),1)
            self.assertEqual(query.call_count,2)
            self.assertIn('first:6,searchWindow:"PT10M"',query.call_args_list[0].args[0])
            rail=query.call_args_list[1].args[1]
            self.assertEqual(rail['prefs']['transit']['transfer']['maximumTransfers'],0)
            self.assertEqual(rail['modes']['transit']['transit'],[{'mode':'SUBWAY'},{'mode':'RAIL'}])
            query.reset_mock()
            server.plan(data,for_optimization=True)
            self.assertIn('first:30,searchWindow:"PT1H"',query.call_args_list[0].args[0])

    def test_fast_replay_retries_full_search_if_filter_removes_every_route(self):
        import server
        data=dict(origin={'lat':22.3,'lon':114.1},destination={'lat':22.4,'lon':114.2},modes=['mtr'],preference='fastest')
        node=dict(end='2026-09-24T10:00:00+08:00',walkDistance=10,legs=[])
        def response(*args, **kwargs):return {'planConnection':{'edges':[{'node':node}], 'routingErrors':[]}}
        with patch.object(server,'validate_request',return_value=datetime.fromisoformat('2026-09-24T09:00:00+08:00')), patch.object(server,'graphql',side_effect=response) as query, patch.object(server,'normalize',side_effect=lambda x,*_:x), patch.object(server,'has_split_mtr_journey',side_effect=[True,False]), patch.object(server,'route_sources',return_value={}):
            result=server.plan(data,for_optimization='fast')
            self.assertEqual(len(result['itineraries']),1)
            self.assertEqual(query.call_count,2)
            self.assertIn('first:30,searchWindow:"PT1H"',query.call_args_list[1].args[0])

    def test_transport_filter_distinguishes_light_rail_and_tram(self):
        self.assertEqual(group(dict(route_type='0',agency_id='RAIL:MTR')),'light_rail')
        self.assertEqual(group(dict(route_type='0',agency_id='TRAM')),'tram')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);source=path/'source.zip';target=path/'filtered.zip'
            with zipfile.ZipFile(source,'w') as z:
                z.writestr('agency.txt','agency_id,agency_name,agency_url,agency_timezone\nRAIL:MTR,MTR,https://www.mtr.com.hk,Asia/Hong_Kong\nTRAM,Tram,https://www.hktramways.com,Asia/Hong_Kong\n')
                z.writestr('routes.txt','route_id,agency_id,route_type\nlr,RAIL:MTR,0\nt,TRAM,0\n')
                z.writestr('trips.txt','route_id,trip_id,service_id\nlr,a,day\nt,b,day\n')
                z.writestr('stop_times.txt','trip_id,stop_id,stop_sequence,arrival_time,departure_time\na,stop,1,09:00:00,09:00:00\nb,stop,1,09:10:00,09:10:00\n')
                z.writestr('frequencies.txt','trip_id,start_time,end_time,headway_secs\na,09:00:00,10:00:00,600\nb,09:00:00,10:00:00,300\n')
            filtered_feed(source,target,['light_rail'])
            with zipfile.ZipFile(target) as z:
                trips=list(csv.DictReader(io.StringIO(z.read('trips.txt').decode())))
                self.assertEqual([t['trip_id'] for t in trips],['a'])
                self.assertIn('09:00:00,10:00:00,600',z.read('frequencies.txt').decode())
                self.assertNotIn('09:10:00',z.read('stop_times.txt').decode())

if __name__=='__main__':unittest.main()
