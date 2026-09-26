import sys
import unittest
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'route_checker'))
from test_optimizer import Router, request
import optimizer as opt
import large_solver

class Cache:
    def __init__(self,seconds=300): self.seconds=seconds; self.flags=[]
    def route(self,*args,**kwargs): raise AssertionError('Large must use fast OTP checks')
    def route_fast(self,a,b,departure,modes,use_cache=True):
        self.flags.append(use_cache)
        return dict(seconds=self.seconds,itinerary=None),False

def seed(spec,emit,cancelled):
    return [dict(fromIndex=i,toIndex=j,seconds=300) for i in range(1,len(spec['points'])) for j in range(1,len(spec['points'])) if i!=j],{}

class LargeTest(unittest.TestCase):
    def data(self,n=25): return dict(request(n),planningMode='large',solverSeconds=2,maxRounds=5)
    def run_plan(self,data,cache=None):
        return opt.run(opt.validate(data,Router),cache or Cache(),lambda *a,**k:None,lambda:False,seed)
    def test_100_jobs_all_checked_once(self):
        d=self.data(100);d['solverSeconds']=4
        result=self.run_plan(d)
        self.assertTrue(result['feasible'],result)
        self.assertEqual(sum(s['jobCount'] for s in result['schedules']),100)
        self.assertLess(result['workersUsed'],20)
        self.assertFalse(result['minimumWorkersProven'])
    def test_limits(self):
        opt.validate(self.data(100),Router)
        with self.assertRaisesRegex(ValueError,'100'):opt.validate(self.data(101),Router)
        with self.assertRaisesRegex(ValueError,'More jobs'):opt.validate(request(21),Router)
    def test_fixed_flexible_and_no_break(self):
        for latest in (None,'2026-09-24T12:30','2026-09-24T14:00'):
            d=self.data()
            if latest:d['break']=dict(minutes=30,earliest='2026-09-24T12:00',latest=latest)
            r=self.run_plan(d)
            self.assertTrue(r['feasible'],r)
            for s in r['schedules']:
                self.assertEqual(sum(e['kind']=='break' for e in s['events']),int(latest is not None))
    def test_windows_bases_and_cache_bypass(self):
        d=self.data(4);d['useTravelCache']=False
        d['workers']=[dict(id='a',startLocation={'lat':22.5,'lon':114.1},endLocation={'lat':22.6,'lon':114.1})]
        d['jobs'][0].update(serviceMinutes=10,window=dict(start='2026-09-24T10:00',end='2026-09-24T10:10'))
        cache=Cache();r=self.run_plan(d,cache)
        self.assertTrue(r['feasible'],r)
        self.assertEqual(cache.flags,[False]*5)
        events=r['schedules'][0]['events']
        self.assertEqual(next(e for e in events if e.get('jobId')=='0')['start'],'2026-09-24T10:00:00+08:00')
        travel=[e for e in events if e['kind']=='travel']
        self.assertEqual(r['points'][travel[0]['fromIndex']]['lat'],22.5)
        self.assertEqual(r['points'][travel[-1]['toIndex']]['lat'],22.6)
    def test_actual_departures_repair(self):
        r=self.run_plan(self.data(),Cache(600))
        self.assertTrue(r['feasible'],r)
        self.assertGreater(r['metrics']['rounds'],1)
    def test_retime_rejects_break_during_travel(self):
        d=self.data(2);d['workers']=[dict(id='a')]
        d['break']=dict(minutes=30,earliest='2026-09-24T09:30',latest='2026-09-24T10:00')
        d['jobs'][0]['window']=dict(start='2026-09-24T09:00',end='2026-09-24T09:20')
        d['jobs'][1]['window']=dict(start='2026-09-24T10:00',end='2026-09-24T10:20')
        spec=opt.validate(d,Router)
        self.assertIsNone(large_solver.retime(spec,[[0,0,0],[0,0,2400],[0,2400,0]],[(0,[1,2])],lambda:False,1))
    def test_heterogeneous_profiles_and_unreachable_base(self):
        d=self.data(2)
        d['workers']=[dict(id='morning',start='2026-09-24T09:00',end='2026-09-24T10:00'),dict(id='afternoon',start='2026-09-24T14:00',end='2026-09-24T15:00')]
        d['jobs'][0]['window']=dict(start='2026-09-24T09:00',end='2026-09-24T09:30')
        d['jobs'][1]['window']=dict(start='2026-09-24T14:00',end='2026-09-24T14:30')
        r=self.run_plan(d)
        self.assertTrue(r['feasible'],r)
        self.assertEqual(r['workersUsed'],2)
        d=self.data(1);d['workers']=[dict(id='only',startLocation={'lat':22.5,'lon':114.1})]
        spec=opt.validate(d,Router)
        r=opt.run(spec,Cache(None),lambda *a,**k:None,lambda:False,seed)
        self.assertFalse(r['feasible'])

    def test_repair_keeps_feasible_fixed_end_base_order(self):
        d=self.data(4)
        d['shift'].update(end='2026-09-24T10:00',endLocation={'lat':22.5,'lon':114.1})
        spec=opt.validate(d,Router)
        matrix=[[0]*6 for _ in range(6)]
        matrix[1][5]=matrix[2][5]=7200
        routes=large_solver.repartition(spec,matrix,[(0,[1,2,3,4])],lambda:False,time.monotonic()+2)
        self.assertIsNotNone(routes)
        self.assertEqual([e['jobIndex'] for e in routes[0]['edges'] if e['jobIndex']],[1,2,3])
        self.assertEqual(len(routes),2)

    def test_cancel(self):
        with self.assertRaises(opt.Cancelled):
            large_solver.solve(opt.validate(self.data(),Router),[],lambda *a,**k:None,lambda:True)

if __name__=='__main__': unittest.main()
