import copy
from datetime import date
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'route_checker'))
from transit_enrichment import timing, merge, check
from transit_enrichment.feed import rows, write_rows
import transit_provenance


def visits(tid='original', duration=600):
    return [dict(trip_id=tid,stop_id=str(i+1),stop_sequence=str(i+1),arrival_time=t,
                 departure_time=t,pickup_type='0',drop_off_type='0',timepoint='1' if t else '0')
            for i,t in enumerate(['08:00:00','','08:10:00'])]


class TimingTests(unittest.TestCase):
    def test_only_blanks_are_reweighted_and_published_total_preserved(self):
        original=visits();current=copy.deepcopy(original)
        current[1].update(arrival_time='08:05:00',departure_time='08:05:00')
        result,accepted,rejected=timing.reweight(original,current,['A','B','C'],{'A':{'B':120},'B':{'C':480}},[0,1000,3000])
        self.assertEqual(result[1]['arrival_time'],'08:02:00')
        self.assertEqual(result[0],original[0]);self.assertEqual(result[2],original[2])
        self.assertEqual(accepted[0]['preserved_duration_seconds'],600);self.assertFalse(rejected)

    def test_no_partial_application_of_missing_conflicting_or_implausible_span(self):
        original=visits();current=copy.deepcopy(original)
        current[1].update(arrival_time='08:05:00',departure_time='08:05:00')
        for weights,conflicts,distances in [({'A':{'B':120}},set(),[0,1000,3000]),
            ({'A':{'B':120},'B':{'C':480}},{('A','B')},[0,1000,3000]),
            ({'A':{'B':600},'B':{'C':600}},set(),[0,1000,3000]),
            ({'A':{'B':120},'B':{'C':480}},set(),None),
            ({'A':{'B':5},'B':{'C':595}},set(),[0,10000,11000])]:
            result,accepted,rejected=timing.reweight(original,current,['A','B','C'],weights,distances,conflicts)
            self.assertEqual(result,current);self.assertFalse(accepted);self.assertTrue(rejected)

    def test_partial_source_time_cannot_be_overwritten(self):
        original=visits();original[1]['arrival_time']='08:03:00'
        current=copy.deepcopy(original);current[1]['departure_time']='08:03:00'
        result,accepted,rejected=timing.reweight(original,current,['A','B','C'],{'A':{'B':120},'B':{'C':480}},[0,1000,3000])
        self.assertEqual(result,current);self.assertEqual(rejected[0]['reason'],'partial_source_timing_inside_span')

    def test_order_and_occurrence_are_required(self):
        current=visits();current[1]['stop_id']='unexpected'
        with self.assertRaises(ValueError):timing.reweight(visits(),current,['A','B','C'],{})


class MergeTests(unittest.TestCase):
    def test_joint_service_cannot_inherit_one_operator_history(self):
        match=dict(operator='KMB')
        self.assertIsNone(merge.historical_match(dict(agency_id='KMB+CTB',matches=[match])))
        self.assertIsNone(merge.historical_match(dict(agency_id='KMB+CTB',matches=[match,dict(operator='CTB')])))
        self.assertEqual(merge.historical_match(dict(agency_id='LWB',matches=[match])),match)

    def test_calendar_override_preserves_old_service_outside_window_and_other_routes(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source=root/'input.zip';output=root/'output.zip'
            trip=dict(route_id='R',service_id='all',trip_id='original',trip_headsign='End',direction_id='0')
            other=dict(trip,route_id='RAIL',trip_id='rail')
            tables={'routes':[dict(route_id='R',agency_id='GMB',route_type='3',route_short_name='112M'),dict(route_id='RAIL',agency_id='MTR',route_type='1',route_short_name='ISL')],
                    'trips':[trip,other],'stop_times':visits()+visits('rail'),
                    'stops':[dict(stop_id=str(i),stop_name=str(i),stop_lat='22.3',stop_lon='114.2') for i in range(1,4)],
                    'frequencies':[dict(trip_id='original',start_time='08:00:00',end_time='22:00:00',headway_secs='1200',exact_times='0')],
                    'calendar':[dict(service_id='all',monday='1',tuesday='1',wednesday='1',thursday='1',friday='1',saturday='1',sunday='1',start_date='20200101',end_date='20991231')],
                    'calendar_dates':[dict(service_id='all',date='20261225',exception_type='2')]}
            with zipfile.ZipFile(source,'w') as z:
                for name,records in tables.items():write_rows(z,name+'.txt',list(records[0]),records)
            db=root/'data/transit_enrichment/raw/identity/routeFareList.min.json';db.parent.mkdir(parents=True);db.write_text('{}')
            key=merge.index_patterns({'trips':[trip],'stop_times':visits()})[2].popitem()[0]
            crosswalk=dict(statistics={},patterns={key:dict(matches=[dict(operator='GMB',stop_visits=[dict(stop_id='GMB:1')])])})
            plan=dict(source=dict(key='GMB:X:1',source_url='https://data.etagmb.gov.hk/route/NT/112M',retrieved_at='2026-10-03',source_files=['sample.json']),
                schedule=dict(days=[dict(date='20261003',departures=[28800],frequencies=[])],warnings=[],policy='official'),
                template='original',trips=['original'],rows=visits(),trip=trip)
            with patch.object(merge.official,'normalize',return_value=dict(patterns=[],source_manifest={},summary={})),patch.object(merge.holidays,'load',return_value=dict(source={})),patch.object(merge.identity,'fetch',return_value={}),patch.object(merge.identity,'match_patterns',return_value=crosswalk),patch.object(merge,'prepare_schedules',return_value=({key:plan},{})):
                merge.compile_base(root,source,output,date(2026,10,3),date(2026,10,3))
            with zipfile.ZipFile(output) as z:
                trips={t['trip_id']:t for t in rows(z,'trips.txt')};dates=list(rows(z,'calendar_dates.txt'))
                self.assertEqual(trips['rail'],other)
                retained=trips['original']['service_id']
                self.assertIn(dict(service_id=retained,date='20261003',exception_type='2'),dates)
                self.assertIn(dict(service_id=retained,date='20261225',exception_type='2'),dates)
                calls=list(rows(z,'stop_times.txt'));self.assertEqual(calls[:6],tables['stop_times'])
                new=next(t for tid,t in trips.items() if tid.startswith('ENR:GMB:'))
                self.assertIn(dict(service_id=new['service_id'],date='20261003',exception_type='1'),dates)
                self.assertEqual([r['arrival_time'] for r in calls[6:]],['08:00:00','','08:10:00'])


class ProvenanceTests(unittest.TestCase):
    def proof(self):
        match=dict(operator='GMB',route='112M',direction='1',service_type=None,evidence='official_full_stop_sequence',source_url='https://data.etagmb.gov.hk',stop_visits=[dict(seq=1,gtfs_stop_id='S',native_stop_id='123',lat=22.3,lon=114.2,name_en='Stop')])
        return dict(trips={'T':'P'},identities=dict(patterns={'P':dict(matches=[match])}))
    def test_repeat_stop_not_silently_first_occurrence(self):
        p=self.proof();p['identities']['patterns']['P']['matches'][0]['stop_visits'].append(dict(p['identities']['patterns']['P']['matches'][0]['stop_visits'][0],seq=9))
        self.assertEqual(transit_provenance.boarding_options(p,'T','S'),[])
        self.assertEqual(transit_provenance.resolve_arrival(p,'T',0,'S',9)[1]['seq'],9)
    def test_client_cannot_use_arbitrary_stops_or_negative_match(self):
        p=self.proof()
        for i,stop,seq in [(-1,'S',1),(1,'S',1),(0,'other',1),(0,'S',2)]:
            with self.assertRaises(ValueError):transit_provenance.resolve_arrival(p,'T',i,stop,seq)
    def test_unverified_identity_not_live(self):
        p=self.proof();p['identities']['patterns']['P']['matches'][0]['evidence']='hkbus_full_stop_sequence'
        self.assertEqual(transit_provenance.boarding_options(p,'T','S'),[])
        with self.assertRaises(ValueError):transit_provenance.resolve_arrival(p,'T',0,'S',1)

if __name__=='__main__':unittest.main()
