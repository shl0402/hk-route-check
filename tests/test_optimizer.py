import sys
import unittest
import tempfile
import time
from unittest.mock import patch
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'route_checker'))
import optimizer as opt

class Router:
    HK=ZoneInfo('Asia/Hong_Kong')
    @staticmethod
    def service_window():
        return datetime(2026,9,1,tzinfo=Router.HK),datetime(2026,10,1,tzinfo=Router.HK)
    @staticmethod
    def validate_request(data):
        for name in ('origin','destination'):
            if not isinstance(data[name],dict) or 'lat' not in data[name]: raise ValueError('point')

def request(n=3):
    return {'jobs':[{'id':str(i),'name':f'Job {i}','lat':22.3+i*.001,'lon':114.1,'serviceMinutes':20} for i in range(n)],
            'shift':{'start':'2026-09-24T09:00','end':'2026-09-24T17:00'},'solverSeconds':2,'modes':['mtr']}

class Cache:
    def __init__(self,seconds=300): self.seconds=seconds
    def route(self,a,b,departure,modes): return {'seconds':self.seconds,'itinerary':None},False

class OptimizerTest(unittest.TestCase):
    def run_plan(self,data,cache=None):
        return opt.run(opt.validate(data,Router),cache or Cache(),lambda *a,**k:None,lambda:False)
    def test_free_endpoints_minimum_and_all_jobs(self):
        result=self.run_plan(request())
        self.assertTrue(result['feasible'])
        self.assertEqual(result['workersUsed'],1)
        self.assertEqual(sorted(e['jobId'] for s in result['schedules'] for e in s['events'] if e['kind']=='job'),['0','1','2'])
    def test_distinct_worker_endpoints(self):
        data=request(2)
        data['workers']=[dict(id='only',start='2026-09-24T09:00',end='2026-09-24T17:00',
                startLocation={'lat':22.32,'lon':114.1,'name':'Start'},endLocation={'lat':22.33,'lon':114.1,'name':'Finish'})]
        result=self.run_plan(data)
        self.assertTrue(result['feasible'])
        trips=[e for e in result['schedules'][0]['events'] if e['kind']=='travel']
        self.assertEqual(len(trips),3)
        self.assertEqual(result['points'][trips[0]['fromIndex']]['name'],'Start')
        self.assertEqual(result['points'][trips[-1]['toIndex']]['name'],'Finish')
    def test_fixed_and_flexible_breaks_never_overlap_work_or_travel(self):
        for latest in ('2026-09-24T09:50','2026-09-24T11:00'):
            data=request(4)
            data['break']={'minutes':30,'earliest':'2026-09-24T09:20','latest':latest}
            result=self.run_plan(data)
            self.assertTrue(result['feasible'])
            for s in result['schedules']:
                breaks=[e for e in s['events'] if e['kind']=='break']
                self.assertEqual(len(breaks),1)
                b=breaks[0]
                for event in s['events']:
                    if event['kind'] in ('travel','job'):
                        self.assertTrue(event['end']<=b['start'] or event['start']>=b['end'])
    def test_short_shifts_need_two_workers(self):
        data=request(2)
        data['shift']['end']='2026-09-24T09:35'
        result=self.run_plan(data)
        self.assertEqual(result['workersUsed'],2)
    def test_job_windows_and_different_service_times(self):
        data=request(2)
        data['jobs'][0].update(serviceMinutes=10,window={'start':'2026-09-24T10:00','end':'2026-09-24T10:10'})
        data['jobs'][1]['serviceMinutes']=35
        result=self.run_plan(data)
        self.assertTrue(result['feasible'])
        job=next(e for s in result['schedules'] for e in s['events'] if e.get('jobId')=='0')
        self.assertIn('T10:00:00',job['start'])
        self.assertEqual(job['seconds'],600)
    def test_time_dependent_repair(self):
        class Changing(Cache):
            def route(self,a,b,departure,modes):
                return {'seconds':300 if departure.hour==9 and departure.minute==0 else 600,'itinerary':None},False
        result=self.run_plan(request(),Changing())
        self.assertTrue(result['feasible'])
        self.assertGreater(result['metrics']['rounds'],1)
    def test_unreachable_required_base_not_reported_success(self):
        data=request(1)
        data['shift']['startLocation']={'lat':22.4,'lon':114.1}
        result=self.run_plan(data,Cache(None))
        self.assertFalse(result['feasible'])
    def test_limit_and_invalid_break(self):
        with self.assertRaises(ValueError): opt.validate(request(21),Router)
        data=request()
        data['break']={'minutes':60,'earliest':'2026-09-24T12:00','latest':'2026-09-24T12:30'}
        with self.assertRaises(ValueError): opt.validate(data,Router)
    def test_otp_timeout_keeps_pair_context_and_never_becomes_zero_cost(self):
        class Slow(Cache):
            def route(self,*args): raise TimeoutError('timed out')
        with self.assertRaisesRegex(opt.RoutingTimeout, 'Job .*No checked schedule'):
            self.run_plan(request(2),Slow())
    def test_timeout_returns_actionable_non_feasible_result(self):
        class Slow(Cache):
            def route(self,*args): raise TimeoutError('timed out')
        with tempfile.TemporaryDirectory() as folder, patch.object(opt,'TravelCache',return_value=Slow()):
            manager=opt.Manager(Router,Path(folder))
            run_id=manager.start(request(2))['id']
            for _ in range(100):
                state=manager.get(run_id)
                if state['status']!='running': break
                time.sleep(.01)
            self.assertEqual(state['result']['status'],'routing_timeout')
            self.assertFalse(state['result']['feasible'])
            self.assertIn('cached',state['result']['recommendation'])
    def test_cancel(self):
        with self.assertRaises(opt.Cancelled):
            opt.run(opt.validate(request(),Router),Cache(),lambda *a,**k:None,lambda:True)
    def test_final_audit_rejects_missing_job(self):
        spec=opt.validate(request(),Router)
        with self.assertRaises(RuntimeError): opt.audit_schedules(spec,[])
    def test_individual_break_overrides_shared_break(self):
        data=request(1)
        data['break']={'minutes':30,'earliest':'2026-09-24T12:00','latest':'2026-09-24T14:00'}
        data['workers']=[{'id':'a','break':None}]
        result=self.run_plan(data)
        self.assertTrue(result['feasible'])
        self.assertFalse(any(e['kind']=='break' for e in result['schedules'][0]['events']))

if __name__=='__main__': unittest.main()
