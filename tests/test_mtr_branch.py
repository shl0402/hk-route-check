import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from compile_mtr_interchanges import journey_variants

class BranchVariantTests(unittest.TestCase):
    def route(self, reverse=False):
        ids = [57,50,49,48,32,31]
        if reverse: ids.reverse()
        return dict(time=18, messages=[dict(msgText='During non-peak hours, please interchange at Tseung Kwan O for trains towards North Point.')],
                    path=[dict(ID=s,lineID=15,time=str(i*3 if i<5 else 18),linkType='RIDE' if i<5 else 'END') for i,s in enumerate(ids)])

    def test_both_directions_keep_whole_journey_time_and_original(self):
        for reverse in (False,True):
            r=self.route(reverse); original=copy.deepcopy(r)
            result=list(journey_variants([r],{'15':'TKL'}))
            self.assertEqual([x[0] for x in result],['0','0:TKO'])
            v=result[1][1]
            self.assertEqual(v['time'],18)
            self.assertEqual([n['time'] for n in v['path']],[n['time'] for n in r['path']])
            self.assertEqual([n['ID'] for n in v['path'] if n['linkType']=='INTERCHANGE'],[50])
            self.assertEqual(r,original)

    def test_no_invented_changes_without_operator_note(self):
        r=self.route();r['messages']=[]
        self.assertEqual(len(list(journey_variants([r],{'15':'TKL'}))),1)

    def test_no_extra_change_for_other_line_or_already_explicit_change(self):
        r=self.route()
        self.assertEqual(len(list(journey_variants([r],{'15':'KTL'}))),1)
        r['path'][1]['linkType']='INTERCHANGE'
        self.assertEqual(len(list(journey_variants([r],{'15':'TKL'}))),1)


class BranchCompilationTests(unittest.TestCase):
    def test_saturday_shuttle_uses_whole_api_cost(self):
        import csv,io,json,tempfile,zipfile
        from compile_mtr_interchanges import compile_interchanges
        def csv_text(rows):
            f=io.StringIO();w=csv.DictWriter(f,list(rows[0]),lineterminator='\n');w.writeheader();w.writerows(rows);return f.getvalue()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'data/mtr_api';(data/'raw').mkdir(parents=True)
            (data/'inventory.json').write_text(json.dumps({'HR':{'metadata':{'lines':[{'ID':15,'alias':'TKL'},{'ID':13,'alias':'ISL'}]}}}))
            (data/'merge_report.json').write_text('{}')
            route=dict(time=37,messages=[{'msgText':'During non-peak hours, please interchange at Tseung Kwan O for trains towards North Point.'}],
                path=[dict(ID=i,lineID=l,time=t,linkType=k) for i,l,t,k in [(57,15,'0','RIDE'),(50,15,'7','RIDE'),(31,13,'18','INTERCHANGE'),(82,13,'37','END')]])
            (data/'raw/HR_57_82.json').write_text(json.dumps(dict(origin=57,destination=82,url='https://www.mtr.com.hk/share/customer/jp/api/HRRoutes/?o=57&d=82&lang=E',fetched_at='2026-09-22',response={'routes':[route]})))
            keys=['TKL:57>50>31','TKL:57>50','TKL:50>31','ISL:31>82']
            ods={f't{i}':dict(journey=k,original_trip=f't{i}') for i,k in enumerate(keys)}
            feed=root/'test.zip'
            tables={
                'trips.txt':[dict(trip_id=f't{i}',route_id='RAIL:MTR:TKL' if i<3 else 'RAIL:MTR:ISL',service_id='MON' if i==0 else 'SAT',trip_headsign='test') for i in range(4)],
                'frequencies.txt':[dict(trip_id=f't{i}',start_time='06:00:00',end_time='23:00:00',headway_secs='300',exact_times='0') for i in range(4)],
                'calendar_dates.txt':[dict(service_id=s,date=d,exception_type='1') for s,d in [('MON','20260928'),('SAT','20260926')]],
                'stops.txt':[dict(stop_id=f'RAIL:MTR:{i}:{l}',stop_name=f'{i} {l}') for i,l in [(57,'TKL'),(50,'TKL'),(31,'TKL'),(31,'ISL'),(82,'ISL')]],
                'stop_times.txt':[dict(trip_id=f't{i}',arrival_time='00:00:00',departure_time='00:00:00',stop_id='RAIL:MTR:57:TKL',stop_sequence='1',pickup_type='0',drop_off_type='0',timepoint='0') for i in range(4)]}
            with zipfile.ZipFile(feed,'w') as z:
                for n,r in tables.items():z.writestr(n,csv_text(r))
                z.writestr('mtr_api_provenance.json',json.dumps(dict(od_trips=ods,od_journeys={})))
            compile_interchanges(root,feed)
            with zipfile.ZipFile(feed) as z:
                proof=json.loads(z.read('mtr_api_provenance.json'))
                j=proof['od_journeys']['JOURNEY:57>82:0:TKO']
                self.assertEqual(j['api_total_seconds'],2220)
                self.assertEqual(j['internal_transfers'],2)
                self.assertEqual(j['seconds'],[0,420,1080,2220])
                self.assertIn('Tseung Kwan O',j['instructions'][0]['text'])
                trips=list(csv.DictReader(io.StringIO(z.read('trips.txt').decode())))
                dates=list(csv.DictReader(io.StringIO(z.read('calendar_dates.txt').decode())))
                tids={t['trip_id'] for t in trips if t['trip_id'].startswith('MTRAPI:57:82:0:TKO:') and any(d['service_id']==t['service_id'] and d['date']=='20260926' for d in dates)}
                self.assertTrue(tids)
                rows=list(csv.DictReader(io.StringIO(z.read('stop_times.txt').decode())))
                for tid in tids:
                    r=[x for x in rows if x['trip_id']==tid]
                    self.assertEqual(len(r),2)
                    self.assertEqual(r[-1]['arrival_time'],'00:37:00')

class WindowTests(unittest.TestCase):
    def test_window_intersection_matches_exhaustive_comparison(self):
        import random
        from compile_mtr_interchanges import intersect_windows,merged
        rng=random.Random(42)
        for _ in range(100):
            left=merged((x,x+rng.randint(1,10)) for x in rng.sample(range(100),20))
            right=merged((x,x+rng.randint(1,10)) for x in rng.sample(range(100),20))
            expected=merged((max(a,c),min(b,d)) for a,b in left for c,d in right if max(a,c)<min(b,d))
            self.assertEqual(intersect_windows(left,right),expected)
        self.assertEqual(intersect_windows([(1,2)],[(2,3)]),[])

if __name__=='__main__': unittest.main()
