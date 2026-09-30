import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/hkbus_pilot'))
from merge_gtfs import verified_n796_circular, WEEK


class N796Departures(unittest.TestCase):
    def setUp(self):
        self.n = dict(title='城巴N796線', groups=[dict(section_path=['服務時間及班次'],
            variant='', errors=[], periods=[], layout='hour_minute', origin='日出康城',
            days=list(range(8)), departures=list(range(85500,101701,1800))+[102900,104100])])
        self.c = dict(gtfs=dict(route_id='8545',agency_id='CTB',route_short_name='N796'),
            government_metadata=[dict(locStartNameC='日出康城',specialType=0,locEndNameC='尖沙咀(循環線)')])
        self.trips = [dict(trip_id='late',service_id='daily'),dict(trip_id='early',service_id='daily')]
        self.st = {t['trip_id']:[dict(stop_id=s) for s in ['a','b','c','a']] for t in self.trips}
        self.tables = {'stops.txt':[dict(stop_id=s,stop_name=n) for s,n in zip('abc',['LOHAS PARK','TSIM SHA TSUI','MONG KOK'])],
            'calendar.txt':[dict(service_id='daily',**{d:'1' for d in WEEK})], 'calendar_dates.txt':[],
            'frequencies.txt':[dict(trip_id='late',start_time='23:45:00',end_time='28:15:00',headway_secs='1800'),
                dict(trip_id='early',start_time='04:15:00',end_time='04:55:00',headway_secs='1200')]}

    def resolve(self):
        return verified_n796_circular(self.n,[self.c],self.tables,self.st,{'8545':self.trips})

    def test_same_loop_despite_different_named_turnaround(self):
        self.assertEqual(self.resolve(),self.c)
        self.assertIn(27*3600+45*60,self.n['groups'][0]['departures'])
        self.assertEqual(len(self.n['groups'][0]['departures']),12)

    def test_special_service_not_given_circular_departures(self):
        self.c['gtfs']['route_id']='8428'
        self.assertIsNone(self.resolve())

    def test_frequency_alone_never_becomes_exact(self):
        self.n['groups'][0]['departures']=[]
        self.assertIsNone(self.resolve())

    def test_changed_timetable_or_stop_pattern_fails_closed(self):
        baseline=copy.deepcopy(self.tables)
        self.tables['frequencies.txt'][0]['headway_secs']='1200'
        self.assertIsNone(self.resolve())
        self.tables=baseline
        self.st['early'][-1]['stop_id']='b'
        self.assertIsNone(self.resolve())

    def test_non_daily_calendar_not_assumed_daily(self):
        self.tables['calendar.txt'][0]['sunday']='0'
        self.assertIsNone(self.resolve())


if __name__=='__main__':unittest.main()
